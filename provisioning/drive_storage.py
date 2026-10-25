"""Bind an existing My Drive folder to one service account.

The operator's only prompt is the browser login. Later runs reuse the saved
user grant. The folder path is never created here: a missing or ambiguous path
stops the run. The service account, its single local key, and the folder
writer grant are reconciled by stable ids.
"""
import base64
import hashlib
import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .state import ProvisioningFailure

REPO = Path(__file__).resolve().parents[1]
DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive'
CLOUD_SCOPE = 'https://www.googleapis.com/auth/cloud-platform'
SCOPES = (DRIVE_SCOPE, CLOUD_SCOPE)


def split_drive_path(path):
    parts = []
    for piece in str(path).replace('\\', '/').split('/'):
        piece = piece.strip()
        if not piece or piece.lower() == 'my drive':
            continue
        if piece in ('.', '..'):
            raise ProvisioningFailure('Drive path cannot contain . or ..')
        parts.append(piece)
    if not parts:
        raise ProvisioningFailure('Drive path must name a folder under My Drive.')
    return tuple(parts)


def project_id_for(folder_id):
    digest = hashlib.sha256(folder_id.encode()).hexdigest()[:10]
    return 'ajss-drive-' + digest


def folder_query(name, parent_id):
    escaped = name.replace('\\', '\\\\').replace("'", "\\'")
    return ("name = '{}' and mimeType = 'application/vnd.google-apps.folder' "
            "and '{}' in parents and trashed = false").format(escaped, parent_id)


def permission_action(permissions, email):
    for item in permissions:
        if item.get('emailAddress', '').lower() != email.lower():
            continue
        if item.get('role') in ('writer', 'fileOrganizer', 'organizer', 'owner'):
            return 'keep', item.get('id')
        return 'upgrade', item.get('id')
    return 'create', None


def enable_google_api(project, service):
    completed = subprocess.run(
        ['gcloud', 'services', 'enable', service, '--project', project],
        capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or '').strip()[-300:]
        raise ProvisioningFailure('Could not enable {} on project {}: {}'.format(service, project, detail))


class Api:
    def __init__(self, access_token, opener=None):
        self.access_token = access_token
        self.opener = opener or urllib.request.urlopen
        self._enabled = set()

    def request(self, method, url, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method, headers={
            'Authorization': 'Bearer ' + self.access_token,
            'Content-Type': 'application/json',
        })
        try:
            with self.opener(request, timeout=60) as response:
                raw = response.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')[:500]
            if 'invalid_grant' in detail:
                raise ProvisioningFailure('Google refresh token is expired or revoked.')
            match = re.search(r'https://console\.developers\.google\.com/apis/api/([a-z0-9.]+)/overview\?project=(\d+)', detail)
            if match and (match.group(2), match.group(1)) not in self._enabled:
                self._enabled.add((match.group(2), match.group(1)))
                enable_google_api(match.group(2), match.group(1))
                return self.request(method, url, body)
            raise ProvisioningFailure('Google returned HTTP {}: {}'.format(exc.code, detail[:300])) from None


def _list_files(api, query):
    matches = []
    page = None
    while True:
        params = {'q': query, 'fields': 'nextPageToken,files(id,name,parents)', 'pageSize': 100,
                  'supportsAllDrives': 'false'}
        if page:
            params['pageToken'] = page
        result = api.request('GET', 'https://www.googleapis.com/drive/v3/files?' + urllib.parse.urlencode(params))
        matches.extend(result.get('files', []))
        page = result.get('nextPageToken')
        if not page:
            return matches


def resolve_folder(api, path):
    parent = 'root'
    folder = None
    for name in split_drive_path(path):
        matches = _list_files(api, folder_query(name, parent))
        if not matches:
            siblings = _list_files(api, "'{}' in parents and mimeType = 'application/vnd.google-apps.folder' "
                                    "and trashed = false".format(parent))
            names = ', '.join(sorted(item['name'] for item in siblings)) or 'no folders'
            raise ProvisioningFailure('Drive folder does not exist: ' + name + '. Folders here: ' + names)
        if len(matches) > 1:
            raise ProvisioningFailure('Drive folder is ambiguous: ' + name)
        folder = matches[0]
        parent = folder['id']
    return folder


def _poll_operation(api, operation, collection):
    name = operation.get('name')
    deadline = time.monotonic() + 180
    while operation.get('done') is not True and name and time.monotonic() < deadline:
        time.sleep(2)
        operation = api.request('GET', collection + name)
    if operation.get('error'):
        raise ProvisioningFailure('Google Cloud operation failed: ' + json.dumps(operation['error'])[:300])
    if operation.get('done') is not True:
        raise ProvisioningFailure('Google Cloud operation is still running. Rerun to resume it.')
    return operation


def ensure_project(api, project_id):
    try:
        return api.request('GET', 'https://cloudresourcemanager.googleapis.com/v1/projects/' + project_id), False
    except ProvisioningFailure as exc:
        if 'HTTP 403' not in str(exc) and 'HTTP 404' not in str(exc):
            raise
    operation = api.request('POST', 'https://cloudresourcemanager.googleapis.com/v1/projects', {
        'projectId': project_id, 'name': 'AJSS Drive',
    })
    _poll_operation(api, operation, 'https://cloudresourcemanager.googleapis.com/v1/')
    project = api.request('GET', 'https://cloudresourcemanager.googleapis.com/v1/projects/' + project_id)
    return project, True


def ensure_service(api, project_id, service):
    name = 'projects/{}/services/{}'.format(project_id, service)
    current = api.request('GET', 'https://serviceusage.googleapis.com/v1/' + name)
    if current.get('state') == 'ENABLED':
        return False
    operation = api.request('POST', 'https://serviceusage.googleapis.com/v1/' + name + ':enable', {})
    _poll_operation(api, operation, 'https://serviceusage.googleapis.com/v1/')
    return True


def ensure_service_account(api, project_id):
    account_id = 'drive-writer'
    email = '{}@{}.iam.gserviceaccount.com'.format(account_id, project_id)
    listed = api.request('GET', 'https://iam.googleapis.com/v1/projects/' + project_id + '/serviceAccounts')
    for account in listed.get('accounts', []):
        if account.get('email') == email:
            return email, False
    api.request('POST', 'https://iam.googleapis.com/v1/projects/' + project_id + '/serviceAccounts', {
        'accountId': account_id, 'serviceAccount': {'displayName': 'AJSS Drive writer'},
    })
    return email, True


def ensure_permission(api, folder_id, email):
    listed = api.request('GET', 'https://www.googleapis.com/drive/v3/files/' + folder_id
                         + '/permissions?fields=permissions(id,emailAddress,role)&supportsAllDrives=false')
    action, permission_id = permission_action(listed.get('permissions', []), email)
    if action == 'keep':
        return False
    if action == 'upgrade':
        api.request('PATCH', 'https://www.googleapis.com/drive/v3/files/' + folder_id
                    + '/permissions/' + permission_id + '?supportsAllDrives=false', {'role': 'writer'})
        return True
    api.request('POST', 'https://www.googleapis.com/drive/v3/files/' + folder_id
                + '/permissions?supportsAllDrives=false&sendNotificationEmail=false', {
                    'type': 'user', 'role': 'writer', 'emailAddress': email,
                })
    return True


def service_account_token(key, opener=None):
    now = int(time.time())
    payload = {
        'iss': key['client_email'], 'scope': DRIVE_SCOPE,
        'aud': 'https://oauth2.googleapis.com/token', 'iat': now, 'exp': now + 3600,
    }
    header = base64.urlsafe_b64encode(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode()).rstrip(b'=')
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).rstrip(b'=')
    signing = header + b'.' + body
    key_path = Path('/tmp') / ('ajss-sa-' + hashlib.sha256(key['private_key'].encode()).hexdigest()[:12] + '.pem')
    try:
        key_path.write_text(key['private_key'])
        key_path.chmod(0o600)
        completed = subprocess.run(
            ['openssl', 'dgst', '-binary', '-sha256', '-sign', str(key_path)],
            input=signing, capture_output=True, check=False)
    finally:
        key_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise ProvisioningFailure('Could not sign the service-account assertion.')
    assertion = signing + b'.' + base64.urlsafe_b64encode(completed.stdout).rstrip(b'=')
    data = urllib.parse.urlencode({
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': assertion.decode(),
    }).encode()
    opener = opener or urllib.request.urlopen
    with opener(urllib.request.Request('https://oauth2.googleapis.com/token', data=data), timeout=30) as response:
        token = json.load(response).get('access_token')
    if not token:
        raise ProvisioningFailure('Service-account token response had no access token.')
    return token


def verify_writer(key, folder_id, opener=None):
    api = Api(service_account_token(key, opener), opener)
    created = api.request('POST', 'https://www.googleapis.com/drive/v3/files?fields=id', {
        'name': '.ajss-write-probe', 'mimeType': 'text/plain', 'parents': [folder_id],
    })
    file_id = created['id']
    api.request('PATCH', 'https://www.googleapis.com/drive/v3/files/' + file_id, {'trashed': True})
    return file_id


def _save(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(path)


def ensure_key(api, project_id, email, key_path):
    if key_path.exists():
        saved = json.loads(key_path.read_text())
        if saved.get('client_email') == email and saved.get('private_key'):
            return False
        raise ProvisioningFailure('The saved service-account key is for a different account. Reconcile it before retrying.')
    created = api.request(
        'POST', 'https://iam.googleapis.com/v1/projects/{}/serviceAccounts/{}/keys'.format(project_id, email),
        {'keyAlgorithm': 'KEY_ALG_RSA_2048'})
    raw = created.get('privateKeyData')
    if not raw:
        raise ProvisioningFailure('Google did not return a service-account key. Rerun only after confirming no key was created.')
    _save(key_path, json.loads(base64.b64decode(raw)))
    return True


def user_access_token(credentials, opener=None):
    data = urllib.parse.urlencode({
        'client_id': credentials['GOOGLE_CLIENT_ID'],
        'client_secret': credentials['GOOGLE_CLIENT_SECRET'],
        'refresh_token': credentials['GOOGLE_REFRESH_TOKEN'],
        'grant_type': 'refresh_token',
    }).encode()
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request('https://oauth2.googleapis.com/token', data=data)
    try:
        with opener(request, timeout=30) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')
        if 'invalid_grant' in detail:
            raise ProvisioningFailure('Google refresh token is expired or revoked.')
        raise ProvisioningFailure('Google token refresh failed.') from None
    if not body.get('access_token'):
        raise ProvisioningFailure('Google token refresh did not return an access token.')
    return body['access_token']


def gcloud_access_token():
    completed = subprocess.run(['gcloud', 'auth', 'print-access-token'], capture_output=True, text=True)
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ProvisioningFailure('gcloud is not logged in. Run gcloud auth login and rerun.')
    return completed.stdout.strip()


def access_scopes(token, opener=None):
    opener = opener or urllib.request.urlopen
    url = 'https://www.googleapis.com/oauth2/v3/tokeninfo?access_token=' + urllib.parse.quote(token)
    with opener(url, timeout=30) as response:
        body = json.load(response)
    return set((body.get('scope') or '').split())


def ensure_drive_login(opener=None):
    """Use the gcloud account. Ask for the Drive scope only when it is missing."""
    token = gcloud_access_token()
    if DRIVE_SCOPE in access_scopes(token, opener):
        return token
    print('Approve Google Drive access in the browser. Cloud login is already present.', flush=True)
    completed = subprocess.run(['gcloud', 'auth', 'login', '--enable-gdrive-access', '--brief'])
    if completed.returncode != 0:
        raise ProvisioningFailure('Google Drive login was not completed.')
    token = gcloud_access_token()
    if DRIVE_SCOPE not in access_scopes(token, opener):
        raise ProvisioningFailure('The login did not grant Google Drive access.')
    return token


def bind(path, state_dir=None, opener=None, login=None):
    """Resolve path, ensure one project, service account, key, and writer grant."""
    state_dir = Path(state_dir or REPO / '.onboarding')
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_path = state_dir / 'drive-service-account.json'
    record_path = state_dir / 'drive-folder.json'
    access = ensure_drive_login(opener) if login is None else login()
    api = Api(access, opener)
    folder = resolve_folder(api, path)
    project_id = project_id_for(folder['id'])
    _, project_created = ensure_project(api, project_id)
    services_enabled = any(ensure_service(api, project_id, name) for name in (
        'iam.googleapis.com', 'drive.googleapis.com'))
    email, account_created = ensure_service_account(api, project_id)
    key_created = ensure_key(api, project_id, email, key_path)
    granted = ensure_permission(api, folder['id'], email)
    key = json.loads(key_path.read_text())
    probe = verify_writer(key, folder['id'], opener)
    _save(record_path, {
        'path': list(split_drive_path(path)), 'folder_id': folder['id'],
        'project_id': project_id, 'service_account': email,
    })
    changed = project_created or services_enabled or account_created or key_created or granted
    return {
        'outcome': 'changed' if changed else 'unchanged',
        'folder_id': folder['id'], 'project_id': project_id,
        'service_account': email, 'write_probe_trashed': probe,
    }
