import json
import tempfile
import urllib.error
from pathlib import Path

from provisioning.contracts import Outcome
from provisioning import vercel_deploy
from provisioning.vercel_deploy import bundle_files, deploy, fingerprint, public_settings


RUNTIME = {
    'google_drive': {
        'artifact_root_url': 'https://drive.google.com/drive/folders/folder',
        'interview_journal_url': 'https://docs.google.com/document/d/journal/edit',
        'completed_journal_url': 'https://docs.google.com/document/d/completed/edit',
    }
}


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def request(self, method, path, body=None, team=None, raw=None, headers=None):
        self.calls.append((method, path.split('?')[0]))
        target = path.split('?')[0]
        for prefix, response in self.pages:
            if target == prefix or target.startswith(prefix + '/'):
                return response
        raise AssertionError(method + ' ' + path)


def test_bundle_contains_the_service_and_not_local_state():
    files = bundle_files()
    assert 'api/index.py' in files
    assert 'application_service/main.py' in files
    assert 'services/api_log.py' in files
    assert 'services/orchestrator.py' in files
    assert 'services/polling.py' in files
    assert 'workflows/00-discover.yaml' in files
    assert 'config/pipelines.json' in files
    assert 'config/runtime.json' not in files
    assert not any(path.startswith('.onboarding') or path.endswith('.pyc') for path in files)


def test_fingerprint_changes_when_a_public_setting_changes():
    files = {'api/index.py': b'handler'}
    first = fingerprint(files, public_settings(RUNTIME))
    other = json.loads(json.dumps(RUNTIME))
    other['google_drive']['artifact_root_url'] = 'https://drive.google.com/drive/folders/other'
    assert first != fingerprint(files, public_settings(other))


def _repo(root):
    repo = Path(root) / 'repo'
    (repo / 'config').mkdir(parents=True)
    (repo / 'config' / 'runtime.json').write_text(json.dumps(RUNTIME))
    return repo


def _patch(files, host):
    vercel_deploy.bundle_files = lambda repo=None: files
    vercel_deploy.load_host = lambda sibling=None: host


def test_deploy_reuses_a_ready_release_with_the_same_fingerprint():
    files = {'api/index.py': b'handler'}
    settings = dict(public_settings(RUNTIME), GOOGLE_SERVICE_ACCOUNT_CLIENT='writer@example.test',
                    AJSS_MCP_ISSUER='https://ajss.vercel.app')
    digest = fingerprint(files, settings)
    vercel_deploy.service_account_key = lambda repo=None: {
        'client_email': 'writer@example.test', 'private_key': 'pem'}
    _patch(files, {'token': 'token', 'project_id': 'prj', 'team_id': 'team', 'name': 'ajss',
                   'base_url': 'https://ajss.vercel.app'})
    client = FakeClient([
        ('/v9/projects/prj/env', {'envs': [
            {'id': 'b', 'key': 'REPOSITORY_BACKEND', 'type': 'plain', 'target': ['production'], 'value': 'hybrid'},
            {'id': 'a', 'key': 'GOOGLE_ARTIFACT_ROOT_URL', 'type': 'plain', 'target': ['production'],
             'value': RUNTIME['google_drive']['artifact_root_url']},
            {'id': 'j', 'key': 'GOOGLE_INTERVIEW_JOURNAL_URL', 'type': 'plain', 'target': ['production'],
             'value': RUNTIME['google_drive']['interview_journal_url']},
            {'id': 'c', 'key': 'GOOGLE_COMPLETED_JOURNAL_URL', 'type': 'plain', 'target': ['production'],
             'value': RUNTIME['google_drive']['completed_journal_url']},
            {'id': 'i', 'key': 'AJSS_MCP_ISSUER', 'type': 'plain', 'target': ['production'],
             'value': 'https://ajss.vercel.app'},
            {'id': 'f', 'key': 'AJSS_WORKLOAD_FILE_BACKEND', 'type': 'plain', 'target': ['production'], 'value': 'drive'},
            {'id': 'v', 'key': 'AJSS_VERCEL_WORKFLOWS_ENABLED', 'type': 'plain', 'target': ['production'], 'value': '0'},
            {'id': 'w', 'key': 'AJSS_WORKFLOW_RUNS_ENABLED', 'type': 'plain', 'target': ['production'], 'value': '0'},
            {'id': 'p', 'key': 'AJSS_MCP_TOOL_PROFILE', 'type': 'plain', 'target': ['production'], 'value': 'domain'},
            {'id': 't', 'key': 'AJSS_API_TOKEN', 'target': ['production']},
            {'id': 'd', 'key': 'AJSS_DATABASE_URL', 'target': ['production']},
            {'id': 's', 'key': 'GOOGLE_SERVICE_ACCOUNT_JSON', 'target': ['production'], 'type': 'sensitive'},
        ]}),
        ('/v6/deployments', {'deployments': [{'uid': 'dpl_ready', 'state': 'READY', 'meta': {'ajssFingerprint': digest}}]}),
        ('/v13/deployments/dpl_ready', {'id': 'dpl_ready', 'readyState': 'READY', 'meta': {'ajssFingerprint': digest}}),
        ('/v9/projects/prj/domains', {'domains': [{'name': 'ajss.vercel.app'}]}),
    ])

    def opener(request, timeout):
        class Response:
            status = 200
            def read(self):
                return b'{"ok": true}'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        if str(request.full_url).endswith('/journal/queues'):
            raise urllib.error.HTTPError(request.full_url, 401, 'no', None, None)
        return Response()

    with tempfile.TemporaryDirectory() as root:
        result = deploy(repo=_repo(root), client=client, opener=opener, prepare=False)
    assert result.outcome == Outcome.UNCHANGED
    assert ('POST', '/v13/deployments') not in client.calls


def test_missing_database_secret_fails_before_upload():
    vercel_deploy.service_account_key = lambda repo=None: {
        'client_email': 'writer@example.test', 'private_key': 'pem'}
    _patch({'api/index.py': b'x'}, {'token': 'token', 'project_id': 'prj', 'team_id': 'team', 'name': 'ajss', 'base_url': None})
    client = FakeClient([
        ('/v9/projects/prj/env', {'envs': [{'key': 'AJSS_API_TOKEN', 'target': ['production']}]}),
    ])
    with tempfile.TemporaryDirectory() as root:
        result = deploy(repo=_repo(root), client=client, prepare=False)
    assert result.outcome == Outcome.FAILED
    assert 'AJSS_DATABASE_URL' in result.evidence[0]


if __name__ == '__main__':
    test_bundle_contains_the_service_and_not_local_state()
    test_fingerprint_changes_when_a_public_setting_changes()
    test_deploy_reuses_a_ready_release_with_the_same_fingerprint()
    test_missing_database_secret_fails_before_upload()
    print('ok')


def test_workflow_execution_settings_are_explicit():
    runtime = json.loads(json.dumps(RUNTIME))
    assert public_settings(runtime)['AJSS_WORKFLOW_RUNS_ENABLED'] == '0'
    assert public_settings(runtime)['AJSS_MCP_TOOL_PROFILE'] == 'domain'
    runtime['chat_workflows'] = {'enabled': True, 'mcp_tool_profile': 'workflows'}
    assert public_settings(runtime)['AJSS_WORKFLOW_RUNS_ENABLED'] == '1'
    assert public_settings(runtime)['AJSS_MCP_TOOL_PROFILE'] == 'workflows'


def test_vercel_requirements_are_self_contained():
    repo = Path(__file__).resolve().parents[1]
    requirements = (repo / 'deployment/vercel/requirements.txt').read_text().splitlines()
    assert not any(line.strip().startswith('-r ') for line in requirements)
    for line in (repo / 'requirements-mcp.txt').read_text().splitlines():
        if line.strip():
            assert line in requirements
