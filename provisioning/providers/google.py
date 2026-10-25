"""Google user consent and idempotent Drive infrastructure provisioning."""
import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from application_service.google_auth import GoogleAccessToken
from ..state import ProvisioningFailure


def browser_credentials(client_id, client_secret, timeout=180, scopes=('https://www.googleapis.com/auth/drive.file',), purpose='Google Drive', port=0):
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    nonce = secrets.token_urlsafe(32)
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if params.get('state') != [nonce]:
                self.send_error(400, 'Invalid authorization state')
                return
            result.update(params)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'Authorization received. Return to the onboarding terminal.')

    with HTTPServer(('127.0.0.1', port), Callback) as server:
        redirect = 'http://127.0.0.1:{}/'.format(server.server_port)
        url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode({
            'client_id': client_id, 'redirect_uri': redirect, 'response_type': 'code',
            'scope': ' '.join(scopes), 'access_type': 'offline',
            'prompt': 'consent', 'state': nonce, 'code_challenge': challenge,
            'code_challenge_method': 'S256',
        })
        print('Authorize ' + purpose + ' in your browser: ' + url, flush=True)
        webbrowser.open(url)
        server.timeout = 1
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result.get('code'):
        raise ProvisioningFailure('Google authorization was not completed. Rerun with a desktop OAuth client or existing refresh credentials.')
    data = urllib.parse.urlencode({
        'client_id': client_id, 'client_secret': client_secret,
        'code': result['code'][0], 'code_verifier': verifier, 'redirect_uri': redirect,
        'grant_type': 'authorization_code',
    }).encode()
    try:
        with urllib.request.urlopen('https://oauth2.googleapis.com/token', data=data, timeout=30) as response:
            tokens = json.load(response)
    except urllib.error.HTTPError:
        raise ProvisioningFailure('Google code exchange failed; check OAuth client type and redirect configuration.') from None
    if not tokens.get('refresh_token'):
        raise ProvisioningFailure('Google did not return an offline refresh token; revoke this app grant and authorize again.')
    granted = {'GOOGLE_CLIENT_ID': client_id, 'GOOGLE_CLIENT_SECRET': client_secret,
               'GOOGLE_REFRESH_TOKEN': tokens['refresh_token']}
    if tokens.get('refresh_token_expires_in'):
        granted['GOOGLE_REFRESH_EXPIRES_IN'] = int(tokens['refresh_token_expires_in'])
    return granted


class DriveProvisioner:
    def __init__(self, credentials):
        self.token = GoogleAccessToken(credentials['GOOGLE_REFRESH_TOKEN'],
                                       credentials['GOOGLE_CLIENT_ID'], credentials['GOOGLE_CLIENT_SECRET'])

    def request(self, method, path, value=None):
        try:
            request = urllib.request.Request('https://www.googleapis.com/drive/v3/' + path,
                method=method, data=json.dumps(value).encode() if value is not None else None,
                headers={'Authorization': 'Bearer ' + self.token.token(), 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')[:300]
            raise ProvisioningFailure('Google Drive returned HTTP {}: {}'.format(exc.code, detail)) from None

    def ensure_folder(self, state, installation_id, name='AI Job Search System', parent=None):
        marker = installation_id.replace("'", "\\'")
        query = "trashed=false and mimeType='application/vnd.google-apps.folder' and appProperties has { key='ajss_installation' and value='%s' }" % marker
        matches = []
        page = None
        while True:
            params = {'q': query, 'fields': 'nextPageToken,files(id,name,parents,appProperties)', 'pageSize': 100}
            if page:
                params['pageToken'] = page
            result = self.request('GET', 'files?' + urllib.parse.urlencode(params))
            matches.extend(result.get('files', []))
            page = result.get('nextPageToken')
            if not page:
                break
        if len(matches) > 1:
            raise ProvisioningFailure('Multiple Drive roots have this installation marker; reconcile them before retrying.')
        saved = state.data.get('drive', {})
        if matches:
            folder = matches[0]
            if saved.get('folder_id') and saved['folder_id'] != folder['id']:
                raise ProvisioningFailure('Drive root differs from the saved binding.')
            if parent and parent not in folder.get('parents', []):
                raise ProvisioningFailure('Existing Drive root is under a different parent.')
            changed = False
        else:
            folder_id = saved.get('folder_id')
            if not folder_id:
                folder_id = self.request('GET', 'files/generateIds?count=1&space=drive')['ids'][0]
                state.data['drive'] = {'folder_id': folder_id, 'pending': True}
                state.checkpoint()
            try:
                folder = self.request('GET', 'files/' + folder_id + '?fields=id,name,parents,appProperties,trashed')
                if folder.get('trashed') or (folder.get('appProperties') or {}).get('ajss_installation') != installation_id:
                    raise ProvisioningFailure('Saved Drive root is trashed or has a different owner marker.')
                changed = False
            except ProvisioningFailure as exc:
                if 'HTTP 404' not in str(exc):
                    raise
                payload = {'id': folder_id, 'name': name, 'mimeType': 'application/vnd.google-apps.folder',
                           'appProperties': {'ajss_installation': installation_id}}
                if parent:
                    payload['parents'] = [parent]
                folder = self.request('POST', 'files?fields=id,name,parents,appProperties', payload)
                changed = True
        state.data['drive'] = {'folder_id': folder['id'], 'pending': False}
        state.checkpoint()
        capabilities = self.request('GET', 'files/' + folder['id'] + '?fields=id,capabilities(canAddChildren,canEdit)')
        if not capabilities.get('capabilities', {}).get('canAddChildren'):
            raise ProvisioningFailure('The Google identity cannot create files in the provisioned root.')
        return folder['id'], changed
