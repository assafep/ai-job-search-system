"""Prepare verified MCP connection files and an optional GPT Action handoff."""
from pathlib import Path
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from .contracts import Outcome, TaskResult
from .bootstrap.handoffs import save
REPO = Path(__file__).resolve().parents[1]
ONBOARDING = REPO / '.onboarding'


def prepare(repo=REPO, onboarding=ONBOARDING, opener=None, mcp_verifier=None):
    opener = opener or urllib.request.urlopen
    try:
        cloud = json.loads((onboarding / 'cloud.json').read_text())
        runtime = cloud.get('runtime') or {}
        url = (runtime.get('base_url') or '').rstrip('/')
        token = runtime.get('bearer_token')
        parsed = urllib.parse.urlsplit(url)
        if not cloud.get('application_ready'):
            raise ValueError('Deploy the Application Service before preparing ChatGPT.')
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('The deployed service needs a public HTTPS URL.')
        if not isinstance(token, str) or not token or any(c in token for c in '\r\n'):
            raise ValueError('The deployment checkpoint has no valid service token.')
        request = urllib.request.Request(url + '/journal/queues', headers={'Authorization': 'Bearer ' + token})
        with opener(request, timeout=45) as response:
            if response.status != 200 or not isinstance(json.loads(response.read()), dict):
                raise ValueError('The authenticated queue check failed.')
        from .mcp_connection import verify
        tool_count = (mcp_verifier or verify)(url, token)
        # Keep the authoritative contract intact; JSON quoting is valid YAML.
        schema = 'servers:\n  - url: ' + json.dumps(url) + '\n' + (repo / 'openapi.yaml').read_text()
        directory = repo / '.onboarding' / 'chatgpt'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        instructions = (
            'Use the Application Service for job-search application state. '
            'Read current state before writing. All mutations must be explicit, validated, '
            'and retry-safe using deterministic idempotency keys where supported. '
            'Never access Google Drive, Gmail, Calendar, or browser APIs directly. '
            'Use listWorkflows, startWorkflow, getWorkflowRun and getWorkflowRunReport for execution. '
            'startWorkflow queues existing Python workflows; omitted limits mean all eligible positions. '
            'Reuse the same idempotency_key on retries, and never retry an uncertain running claim. '
            'Return readable PDF reports. Prepared queue membership '
            'authorizes automatic submission by the workflow runtime; do not introduce '
            'a human approval gate. Unconfirmed submissions require manual reconciliation.\n'
        )
        guide = (
            'MCP endpoint verified: ' + url + '/mcp\n'
            'The ChatGPT connection still needs its one-time UI setup.\n\n'
            '1. On ChatGPT web, open Plugins, +, Add custom MCP server.\n'
            '2. Use the URL above and select OAuth. Leave client credentials empty for dynamic registration.\n'
            '3. Connect, then enter your existing Application Service token on the AJSS authorization page.\n'
            '4. Test queue counts. Workspace permissions determine available read/write tools.\n\n'
            'Codex: run ./deployment/ctl.sh codex on the host to register automatically.\n'
            'The stdio launcher reads the saved deployment URL and credential at startup.\n'
            'mcp.json is a local stdio configuration with no embedded credential.\n\n'
            'GPT Action fallback: paste openapi.yaml in a GPT Action, choose API key / Bearer, '
            'paste the token from authentication.json, and add instructions.txt.\n'
            'Keep authentication.json private. Regenerate with ./deployment/ctl.sh chatgpt '
            'after any URL, token, or API change.\n'
        )
        outputs = {'openapi.yaml': schema, 'instructions.txt': instructions, 'SETUP.txt': guide,
                   'mcp.json': json.dumps({'mcpServers': {'ajss-application-service': {
                       'command': str(repo / 'deployment' / 'ctl.sh'), 'args': ['mcp-stdio']} }}, indent=2) + '\n',
                   'connection.json': json.dumps({'server_url': url + '/mcp', 'authentication': 'oauth',
                                                  'credential_file': 'authentication.json'}, indent=2) + '\n',
                   'authentication.json': json.dumps({'type': 'bearer', 'token': token}, indent=2) + '\n'}
        changed = False
        for name, content in outputs.items():
            path = directory / name
            if not path.exists() or path.read_text() != content:
                # Use the same atomic private writer as the deployment checkpoint.
                fd, temporary = tempfile.mkstemp(dir=directory)
                try:
                    with os.fdopen(fd, 'w') as stream:
                        stream.write(content)
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                changed = True
            path.chmod(0o600)
        save(directory / 'verified.json', {'verified_at': time.time(), 'server_url': url + '/mcp',
                                           'tool_count': tool_count})
        return TaskResult(Outcome.CHANGED if changed else Outcome.UNCHANGED, None, (
            'authenticated REST and MCP queue reads verified; ' + str(tool_count) + ' tools discovered',
            'ChatGPT / Codex connection files: ' + str(directory),
            'Finish the one-time ChatGPT UI setup described in SETUP.txt; token omitted from output.',
        ), None)
    except Exception as exc:
        # Never include request exceptions or checkpoint values: they can contain secrets.
        if isinstance(exc, urllib.error.HTTPError):
            detail = 'Authenticated API verification returned HTTP ' + str(exc.code) + '.'
            exc.close()
        else:
            detail = 'Could not prepare ChatGPT connection. Check the deployed service, checkpoint, and local file access.'
        return TaskResult(Outcome.FAILED, None, (detail,),
                          'Fix the deployment and rerun ./deployment/ctl.sh chatgpt.')
