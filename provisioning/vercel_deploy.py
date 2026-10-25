"""Deploy the Application Service onto the existing Vercel project.

Host provisioning stays in the sibling bootstrap. This step only uploads an
immutable production release of the Python service and reuses that release
when the source and public settings are unchanged.
"""
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .contracts import Outcome, TaskResult

REPO = Path(__file__).resolve().parents[1]
SIBLING = REPO.parent / 'auto-signup-vercel'
API = 'https://api.vercel.com'
FINGERPRINT_KEY = 'ajssFingerprint'
SERVICE_FILES = (
    'services/__init__.py',
    'services/activity_log.py',
    'services/api_log.py',
)
PUBLIC_SETTINGS = (
    ('REPOSITORY_BACKEND', 'hybrid'),
    ('GOOGLE_ARTIFACT_ROOT_URL', ('google_drive', 'artifact_root_url')),
    ('GOOGLE_INTERVIEW_JOURNAL_URL', ('google_drive', 'interview_journal_url')),
    ('GOOGLE_COMPLETED_JOURNAL_URL', ('google_drive', 'completed_journal_url')),
)


def _result(outcome, evidence, resume=None):
    return TaskResult(outcome, None, tuple(evidence), resume)


class VercelError(Exception):
    pass


def bundle_files(repo=REPO):
    """Return deploy paths mapped to file bytes. No secrets or local state."""
    files = {}
    vercel = repo / 'deployment' / 'vercel'
    for path in sorted(vercel.rglob('*')):
        if path.is_file() and path.suffix != '.pyc' and '__pycache__' not in path.parts:
            files[str(path.relative_to(vercel))] = path.read_bytes()
    package = repo / 'application_service'
    for path in sorted(package.rglob('*.py')):
        if '__pycache__' in path.parts:
            continue
        files[str(path.relative_to(repo))] = path.read_bytes()
    for directory in ('services', 'workflows', 'agents', 'schemas', 'adapters'):
        for path in sorted((repo / directory).rglob('*')):
            if path.is_file() and path.suffix in {'.py', '.yaml', '.json', '.md'} and '__pycache__' not in path.parts:
                files[str(path.relative_to(repo))] = path.read_bytes()
    for relative in ('config/pipelines.json', 'config/discovery-sources.json', 'config/automation-policy.yaml', 'openapi.yaml', 'requirements-mcp.txt'):
        path = repo / relative
        if path.is_file():
            files[relative] = path.read_bytes()
    return files


def public_settings(runtime):
    drive = runtime.get('google_drive') or {}
    settings = {}
    for key, source in PUBLIC_SETTINGS:
        settings[key] = source if isinstance(source, str) else drive.get(source[1]) or ''
    chat = runtime.get('chat_workflows') or {}
    settings['AJSS_LEGACY_WORKSTATION_WORKFLOWS'] = '1' if (runtime.get('workflow_execution') or {}).get('legacy_workstation_enabled') is True else '0'
    if settings['AJSS_LEGACY_WORKSTATION_WORKFLOWS'] == '1' and (runtime.get('workflow_execution') or {}).get('vercel_enabled') is True:
        raise VercelError('Choose Vercel execution or explicit legacy workstation mode, not both')
    settings['AJSS_VERCEL_WORKFLOWS_ENABLED'] = '1' if (runtime.get('workflow_execution') or {}).get('vercel_enabled') is True else '0'
    settings['AJSS_WORKFLOW_RUNS_ENABLED'] = '1' if chat.get('enabled') is True else '0'
    settings['AJSS_MCP_TOOL_PROFILE'] = chat.get('mcp_tool_profile', 'domain')
    settings['AJSS_WORKLOAD_FILE_BACKEND'] = chat.get('internal_file_backend', 'drive')
    if settings['AJSS_WORKLOAD_FILE_BACKEND'] not in ('drive', 'database'):
        raise VercelError('chat_workflows.internal_file_backend must be drive or database')
    if settings['AJSS_MCP_TOOL_PROFILE'] not in ('domain', 'workflows'):
        raise VercelError('chat_workflows.mcp_tool_profile must be domain or workflows')
    missing = [key for key, value in settings.items() if not value]
    if missing:
        raise VercelError('runtime.json is missing ' + ', '.join(missing))
    return settings


def fingerprint(files, settings):
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(b'\0')
        digest.update(hashlib.sha1(files[path]).digest())
    for key in sorted(settings):
        digest.update(key.encode())
        digest.update(b'=')
        digest.update(settings[key].encode())
        digest.update(b'\0')
    return digest.hexdigest()


def load_host(sibling=SIBLING):
    path = sibling / '.onboarding' / 'cloud.json'
    auth_path = sibling / '.onboarding' / 'vercel-auth' / 'auth.json'
    if not path.is_file():
        raise VercelError('The Vercel project checkpoint is missing. Run host provisioning first.')
    state = json.loads(path.read_text())
    token = os.environ.get('VERCEL_TOKEN')
    if not token and auth_path.is_file():
        token = json.loads(auth_path.read_text()).get('token')
    if not token:
        token = (state.get('authentication') or {}).get('access_token')
    project_id = state.get('project_id')
    team_id = state.get('team_id')
    name = state.get('project_name')
    if not token or not project_id or not team_id or not name:
        raise VercelError('The Vercel checkpoint has no project or login. Rerun host provisioning.')
    return {'token': token, 'project_id': project_id, 'team_id': team_id, 'name': name,
            'base_url': (state.get('runtime') or {}).get('base_url')}


class Client:
    def __init__(self, token, opener=None):
        self.token = token
        self.opener = opener or urllib.request.urlopen

    def request(self, method, path, body=None, team=None, raw=None, headers=None):
        if team:
            path += ('&' if '?' in path else '?') + urllib.parse.urlencode({'teamId': team})
        extra = {'Authorization': 'Bearer ' + self.token, 'User-Agent': 'ajss-deploy/1'}
        if raw is None:
            extra['Content-Type'] = 'application/json'
            data = None if body is None else json.dumps(body).encode()
        else:
            data = raw
        if headers:
            extra.update(headers)
        request = urllib.request.Request(API + path, data=data, method=method, headers=extra)
        try:
            with self.opener(request, timeout=60) as response:
                payload = response.read()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            exc.close()
            raise VercelError('Vercel returned HTTP %s for %s %s.' % (exc.code, method, path.split('?')[0])) from None


def ensure_settings(client, host, settings):
    environment = client.request('GET', '/v9/projects/' + host['project_id'] + '/env', team=host['team_id'])
    envs = environment.get('envs') or []
    for key in ('AJSS_API_TOKEN', 'AJSS_DATABASE_URL'):
        if not any(item.get('key') == key and 'production' in item.get('target', []) for item in envs):
            raise VercelError(key + ' is not published on the Vercel project.')
    changed = False
    for key, value in settings.items():
        current = [item for item in envs if item.get('key') == key and 'production' in item.get('target', [])
                   and not item.get('gitBranch')]
        if len(current) > 1:
            raise VercelError('Multiple production ' + key + ' variables exist.')
        if not current:
            client.request('POST', '/v10/projects/' + host['project_id'] + '/env', {
                'key': key, 'value': value, 'type': 'plain', 'target': ['production'],
            }, team=host['team_id'])
            changed = True
            continue
        # Plain values are on the list response. The decrypt endpoint does not
        # return them, and treating that empty result as a change redeploys forever.
        if current[0].get('type') != 'plain':
            raise VercelError(key + ' must be a plain production variable.')
        if current[0].get('value') != value:
            client.request('PATCH', '/v9/projects/' + host['project_id'] + '/env/' + current[0]['id'],
                           {'value': value}, team=host['team_id'])
            changed = True
    return changed


def service_account_key(repo=REPO):
    path = repo / '.onboarding' / 'drive-service-account.json'
    if not path.is_file():
        raise VercelError('The Drive service-account key is missing. Rerun ./deployment/ctl.sh drive.')
    info = json.loads(path.read_text())
    if not info.get('client_email') or not info.get('private_key'):
        raise VercelError('The Drive service-account key is incomplete.')
    return info


USER_DRIVE_ENV = (
    'GOOGLE_DRIVE_FILE_CLIENT_ID',
    'GOOGLE_DRIVE_FILE_CLIENT_SECRET',
    'GOOGLE_DRIVE_FILE_REFRESH_TOKEN',
)


def user_drive_credentials(repo=REPO):
    path = repo / '.onboarding' / 'drive-user.json'
    if not path.is_file():
        return None
    saved = json.loads(path.read_text())
    values = (saved.get('client_id'), saved.get('client_secret'), saved.get('refresh_token'))
    if not all(values):
        raise VercelError('The drive.file login file is incomplete. Rerun ./deployment/ctl.sh drive-login.')
    return dict(zip(USER_DRIVE_ENV, values))


def ensure_user_drive(client, host, credentials):
    """Publish the drive.file refresh token when it is not already in production."""
    if not credentials:
        return False
    environment = client.request('GET', '/v9/projects/' + host['project_id'] + '/env', team=host['team_id'])
    existing = {item.get('key') for item in environment.get('envs') or []
                if 'production' in item.get('target', []) and not item.get('gitBranch')}
    changed = False
    for key, value in credentials.items():
        if key in existing:
            continue
        client.request('POST', '/v10/projects/' + host['project_id'] + '/env', {
            'key': key, 'value': value, 'type': 'sensitive', 'target': ['production'],
        }, team=host['team_id'])
        changed = True
    return changed


def ensure_cron_secret(client, host):
    """Create one private schedule credential. Never return or log its value."""
    import secrets
    environment = client.request('GET', '/v9/projects/' + host['project_id'] + '/env', team=host['team_id'])
    if any(item.get('key') == 'CRON_SECRET' and 'production' in item.get('target', [])
           for item in environment.get('envs', [])):
        return False
    client.request('POST', '/v10/projects/' + host['project_id'] + '/env', {
        'key': 'CRON_SECRET', 'value': secrets.token_urlsafe(32), 'type': 'sensitive', 'target': ['production'],
    }, team=host['team_id'])
    return True


def ensure_service_account(client, host, info):
    environment = client.request('GET', '/v9/projects/' + host['project_id'] + '/env', team=host['team_id'])
    current = [item for item in environment.get('envs') or []
               if item.get('key') == 'GOOGLE_SERVICE_ACCOUNT_JSON' and 'production' in item.get('target', [])
               and not item.get('gitBranch')]
    if len(current) > 1:
        raise VercelError('Multiple production GOOGLE_SERVICE_ACCOUNT_JSON variables exist.')
    if current:
        return False
    client.request('POST', '/v10/projects/' + host['project_id'] + '/env', {
        'key': 'GOOGLE_SERVICE_ACCOUNT_JSON',
        'value': json.dumps(info),
        'type': 'sensitive',
        'target': ['production'],
    }, team=host['team_id'])
    return True


def upload(client, host, files):
    uploaded = []
    for path in sorted(files):
        content = files[path]
        digest = hashlib.sha1(content).hexdigest()
        client.request('POST', '/v2/files', team=host['team_id'], raw=content, headers={
            'Content-Type': 'application/octet-stream',
            'x-vercel-digest': digest,
        })
        uploaded.append({'file': path, 'sha': digest, 'size': len(content)})
    return uploaded


def matching_release(client, host, digest):
    page = client.request('GET', '/v6/deployments?' + urllib.parse.urlencode({
        'projectId': host['project_id'], 'target': 'production', 'limit': 5,
    }), team=host['team_id'])
    for item in page.get('deployments') or []:
        if (item.get('meta') or {}).get(FINGERPRINT_KEY) != digest:
            continue
        if item.get('state') in (None, 'READY') or item.get('readyState') == 'READY':
            deployment = client.request('GET', '/v13/deployments/' + item['uid'], team=host['team_id'])
            status = deployment.get('readyState') or deployment.get('status')
            if status == 'READY' and (deployment.get('meta') or {}).get(FINGERPRINT_KEY) == digest:
                return deployment
    return None


def wait_ready(client, host, deployment_id):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        deployment = client.request('GET', '/v13/deployments/' + deployment_id, team=host['team_id'])
        status = deployment.get('readyState') or deployment.get('status')
        if status == 'READY':
            return deployment
        if status in ('ERROR', 'CANCELED'):
            raise VercelError('Application Service deployment failed. Inspect its build logs in Vercel.')
        time.sleep(3)
    raise VercelError('Application Service deployment is still pending after 10 minutes.')


def production_url(client, host, deployment):
    domains = client.request('GET', '/v9/projects/' + host['project_id'] + '/domains', team=host['team_id'])
    names = [item['name'] for item in domains.get('domains') or []
             if not item.get('gitBranch') and str(item.get('name', '')).endswith('.vercel.app')]
    if names:
        return 'https://' + names[0]
    if deployment.get('url'):
        return 'https://' + deployment['url']
    if host.get('base_url'):
        return host['base_url']
    raise VercelError('No production URL is assigned yet.')


def verify(url, opener=None):
    opener = opener or urllib.request.urlopen
    health = urllib.request.Request(url + '/health')
    try:
        with opener(health, timeout=45) as response:
            body = json.loads(response.read().decode())
            if response.status != 200 or body.get('ok') is not True:
                raise VercelError('The deployed service did not report healthy.')
    except urllib.error.HTTPError as exc:
        exc.close()
        raise VercelError('Health check returned HTTP %s.' % exc.code) from None
    private = urllib.request.Request(url + '/journal/queues')
    try:
        opener(private, timeout=45).close()
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 401:
            return
        raise VercelError('Unauthenticated queue check returned HTTP %s.' % exc.code) from None
    raise VercelError('The deployed service allowed an unauthenticated queue read.')


def database_urls(sibling=SIBLING):
    path = sibling / '.onboarding' / 'cloud.json'
    if not path.is_file():
        raise VercelError('The database checkpoint is missing. Rerun host provisioning.')
    state = json.loads(path.read_text())
    connections = ((state.get('database') or {}).get('common') or {}).get('connections') or {}
    admin = (connections.get('admin') or {}).get('url')
    app = (connections.get('app') or {}).get('url')
    role = (connections.get('app') or {}).get('role')
    if not role and app:
        role = urllib.parse.unquote(urllib.parse.urlsplit(app).username or '')
    if not admin or not app or not role:
        raise VercelError('The database checkpoint has no admin and application connections.')
    return admin, app, role


def ensure_docs_api(repo=REPO):
    """The journal is a Google Doc. Drive access does not enable the Docs API."""
    path = repo / '.onboarding' / 'drive-folder.json'
    if not path.is_file():
        raise VercelError('Drive project state is missing. Rerun ./deployment/ctl.sh drive.')
    project = json.loads(path.read_text()).get('project_id')
    if not project:
        raise VercelError('Drive project state has no project id.')
    completed = subprocess.run(
        ['gcloud', 'services', 'enable', 'docs.googleapis.com', '--project', project],
        capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or 'gcloud could not enable docs.googleapis.com').strip().splitlines()
        raise VercelError(detail[-1][:300])


def initialize_storage(app_url, info, settings):
    previous = {key: os.environ.get(key) for key in (
        'AJSS_DATABASE_URL', 'GOOGLE_SERVICE_ACCOUNT_JSON', 'GOOGLE_ARTIFACT_ROOT_URL',
        'GOOGLE_INTERVIEW_JOURNAL_URL', 'REPOSITORY_BACKEND')}
    os.environ['AJSS_DATABASE_URL'] = app_url
    os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'] = json.dumps(info)
    os.environ['GOOGLE_ARTIFACT_ROOT_URL'] = settings['GOOGLE_ARTIFACT_ROOT_URL']
    os.environ['GOOGLE_INTERVIEW_JOURNAL_URL'] = settings['GOOGLE_INTERVIEW_JOURNAL_URL']
    os.environ['REPOSITORY_BACKEND'] = 'hybrid'
    try:
        from application_service.initialization import initialize
        return initialize()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def remember_release(sibling, deployment_id, url=None):
    path = sibling / '.onboarding' / 'cloud.json'
    if not path.is_file():
        return
    state = json.loads(path.read_text())
    state['application_ready'] = True
    state['deployment_id'] = deployment_id
    runtime = state.get('runtime') or {}
    runtime['verification'] = 'application_service'
    if url:
        runtime['base_url'] = url
    state['runtime'] = runtime
    from .bootstrap.handoffs import save
    save(path, state)


def deploy(repo=REPO, sibling=SIBLING, client=None, opener=None, wait=wait_ready, prepare=True):
    try:
        runtime = json.loads((repo / 'config' / 'runtime.json').read_text())
        settings = public_settings(runtime)
        host = load_host(sibling)
        if host.get('base_url'):
            settings['AJSS_MCP_ISSUER'] = host['base_url'].rstrip('/')
        account = service_account_key(repo)
        settings = dict(settings, GOOGLE_SERVICE_ACCOUNT_CLIENT=account['client_email'])
        files = bundle_files(repo)
        digest = fingerprint(files, settings)
        own_client = client is None
        client = client or Client(host['token'], opener)
        if prepare:
            from .schema_migrations import MigrationError, apply_migrations
            admin_url = app_url = ''
            try:
                admin_url, app_url, app_role = database_urls(sibling)
                apply_migrations(admin_url, app_role, repo)
                ensure_docs_api(repo)
                storage = initialize_storage(app_url, account, settings)
            except VercelError:
                raise
            except MigrationError as exc:
                raise VercelError(str(exc)) from None
            except Exception as exc:
                text = str(exc).replace(admin_url, '<database>').replace(app_url, '<database>')
                raise VercelError(text[:400]) from None
        else:
            storage = None
        settings_changed = ensure_settings(client, host, {key: value for key, value in settings.items()
                                                          if key != 'GOOGLE_SERVICE_ACCOUNT_CLIENT'})
        account_changed = ensure_service_account(client, host, account)
        user_changed = ensure_user_drive(client, host, user_drive_credentials(repo))
        cron_changed = ensure_cron_secret(client, host) if json.loads(files.get('vercel.json', b'{}')).get('crons') else False
        existing = None if settings_changed or account_changed or user_changed or cron_changed else matching_release(client, host, digest)
        if existing:
            url = production_url(client, host, existing)
            verify(url, opener)
            if own_client:
                remember_release(sibling, existing.get('id') or existing.get('uid') or '', url)
            evidence = (
                'release ' + existing.get('id', existing.get('uid', '')),
                'fingerprint ' + digest,
                'endpoint ' + url,
                'health ok; unauthenticated queue read rejected',
            )
            if storage and storage.get('document_id'):
                evidence += ('journal ' + storage['document_id'],)
            return _result(Outcome.UNCHANGED, evidence)
        uploaded = upload(client, host, files)
        created = client.request('POST', '/v13/deployments', {
            'name': host['name'],
            'project': host['project_id'],
            'target': 'production',
            'files': uploaded,
            'meta': {FINGERPRINT_KEY: digest},
            'projectSettings': {'framework': None},
        }, team=host['team_id'])
        deployment_id = created.get('id') or created.get('uid')
        if not deployment_id:
            raise VercelError('Vercel did not return a deployment id.')
        ready = wait(client, host, deployment_id)
        url = production_url(client, host, ready)
        verify(url, opener)
        if own_client:
            remember_release(sibling, ready.get('id') or deployment_id, url)
    except VercelError as exc:
        return _result(Outcome.FAILED, (str(exc),),
                       'Rerun ./deployment/ctl.sh up after the reported condition is fixed.')
    evidence = [
        'release ' + (ready.get('id') or deployment_id),
        'fingerprint ' + digest,
        'endpoint ' + url,
        'health ok; unauthenticated queue read rejected',
    ]
    if storage and storage.get('document_id'):
        evidence.append('journal ' + storage['document_id'])
    return _result(Outcome.CHANGED, tuple(evidence))
