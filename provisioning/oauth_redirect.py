"""Register the Gmail renewal callback on the project's OAuth client during onboarding."""
import json
import urllib.error
import urllib.parse
import urllib.request

from .drive_login import CLIENT_PATH
from .drive_storage import gcloud_access_token
from .state import ProvisioningFailure

CALLBACK = None  # Set to the deployed service URL + '/gmail/callback'
LOOPBACK = 'http://127.0.0.1:8765/'


def _request(opener, token, method, url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json',
    })
    try:
        with opener(request, timeout=30) as response:
            raw = response.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:300]
        raise ProvisioningFailure('OAuth client update failed: HTTP {} {}'.format(exc.code, detail)) from None


def _section(data, key):
    section = data.get(key)
    return section if isinstance(section, dict) else None


def ensure_callback(path=CLIENT_PATH, callback=CALLBACK, loopback=LOOPBACK, opener=None, token=None, project_id=None):
    """Add the renewal callback and the fixed loopback to a web OAuth client.

    Returns the client id and secret onboarding must use for Gmail. A desktop
    client cannot carry an https callback, so this creates a web client when
    the downloaded file has only an installed client.
    """
    opener = opener or urllib.request.urlopen
    if not path.is_file():
        raise ProvisioningFailure('The OAuth client file is missing. Finish the desktop client download, then rerun onboarding.')
    data = json.loads(path.read_text())
    web = _section(data, 'web')
    installed = _section(data, 'installed')
    source = web or installed or data
    project = project_id or source.get('project_id')
    if not project:
        raise ProvisioningFailure('The OAuth client file has no project_id.')
    if web and callback in (web.get('redirect_uris') or []) and loopback in (web.get('redirect_uris') or []):
        return web['client_id'], web['client_secret']
    access = token if token is not None else gcloud_access_token()
    project_info = _request(
        opener, access, 'GET', 'https://cloudresourcemanager.googleapis.com/v1/projects/' + urllib.parse.quote(project))
    number = project_info.get('projectNumber')
    if not number:
        raise ProvisioningFailure('Google did not return a project number for ' + project)
    brands = _request(
        opener, access, 'GET', 'https://clientauthconfig.googleapis.com/v1/projects/{}/brands'.format(number))
    brand_list = brands.get('brands') or []
    if not brand_list:
        raise ProvisioningFailure('The Google project has no OAuth brand. Finish the consent screen, then rerun onboarding.')
    brand = brand_list[0]['name']
    listed = _request(opener, access, 'GET', 'https://clientauthconfig.googleapis.com/v1/' + brand + '/clients')
    clients = listed.get('clients') or []
    wanted = [callback, loopback]
    match = None
    for client in clients:
        if client.get('clientId') == (web or source).get('client_id') or callback in (client.get('redirectUris') or []):
            match = client
            break
    uris = list(dict.fromkeys((match.get('redirectUris') if match else []) + wanted))
    if match:
        updated = _request(
            opener, access, 'PATCH',
            'https://clientauthconfig.googleapis.com/v1/' + match['name'] + '?updateMask=redirectUris',
            {'redirectUris': uris})
        client_id = updated.get('clientId') or match.get('clientId')
        secret = (web or source).get('client_secret') or updated.get('secret')
    else:
        created = _request(opener, access, 'POST', 'https://clientauthconfig.googleapis.com/v1/' + brand + '/clients', {
            'displayName': 'AJSS Gmail',
            'type': 'WEB',
            'redirectUris': uris,
        })
        client_id = created.get('clientId')
        secret = created.get('secret')
    if not client_id or not secret:
        raise ProvisioningFailure('Google did not return a web OAuth client id and secret.')
    data['web'] = {
        'client_id': client_id,
        'client_secret': secret,
        'project_id': project,
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'redirect_uris': uris,
    }
    path.write_text(json.dumps(data, indent=2) + '\n')
    path.chmod(0o600)
    return client_id, secret
