"""One-time Google login for files this app creates.

The scope is drive.file. The refresh token cannot list or edit the rest of
the user's Drive. New completion files use this token. The service account
keeps editing the existing journal.
"""
import json
import os
from pathlib import Path

from .providers.google import browser_credentials
from .state import ProvisioningFailure

REPO = Path(__file__).resolve().parents[1]
CLIENT_PATH = REPO / '.onboarding' / 'oauth-client.json'
TOKEN_PATH = REPO / '.onboarding' / 'drive-user.json'
RUNTIME_PATH = REPO / 'config' / 'runtime.json'


def file_login_instructions(email, project_id=None):
    """Steps for the person being onboarded. The test user is their email."""
    if not email:
        raise ProvisioningFailure(
            'The applicant profile has no personal.email. Add that address, then rerun Drive setup.')
    query = '?project=' + project_id if project_id else ''
    clients = 'https://console.cloud.google.com/auth/clients' + query
    audience = 'https://console.cloud.google.com/auth/audience' + query
    return '\n'.join((
        'Google needs a one-time file-access approval for ' + email + '.',
        '1. Open ' + clients + ' and create an OAuth client of type Desktop.',
        '2. Download the JSON to .onboarding/oauth-client.json.',
        '3. Open ' + audience + '. Leave the app External and in Testing.',
        '4. Add ' + email + ' under Test users and save.',
        '5. Rerun Drive setup and choose Allow for ' + email + '.',
    ))


def applicant_email(runtime_path=RUNTIME_PATH):
    if not runtime_path.is_file():
        raise ProvisioningFailure('Finish onboarding before Drive file login so the test user is the applicant email.')
    config = json.loads(runtime_path.read_text())
    relative = ((config.get('google_drive') or {}).get('applicant_profile_path')) or ''
    profile_path = Path(relative) if os.path.isabs(relative) else REPO / relative
    if not relative or not profile_path.is_file():
        raise ProvisioningFailure('The applicant profile is missing. Finish onboarding before Drive file login.')
    profile = json.loads(profile_path.read_text())
    return ((profile.get('personal') or {}).get('email') or '').strip()


def client_credentials(path=CLIENT_PATH, email=None, project_id=None):
    if not path.is_file():
        raise ProvisioningFailure(file_login_instructions(email, project_id))
    data = json.loads(path.read_text())
    installed = data.get('installed') or data.get('web') or data
    client_id = installed.get('client_id')
    client_secret = installed.get('client_secret')
    if not client_id or not client_secret:
        raise ProvisioningFailure('The OAuth client file needs client_id and client_secret.')
    return client_id, client_secret


def save_user_token(credentials, path=TOKEN_PATH):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({
        'client_id': credentials['GOOGLE_CLIENT_ID'],
        'client_secret': credentials['GOOGLE_CLIENT_SECRET'],
        'refresh_token': credentials['GOOGLE_REFRESH_TOKEN'],
    }))
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def token_saved(path=TOKEN_PATH):
    if not path.is_file():
        return False
    saved = json.loads(path.read_text())
    return all(saved.get(key) for key in ('client_id', 'client_secret', 'refresh_token'))


def ensure_file_login(project_id=None, email=None):
    """Ask for the drive.file grant once. Later provisioning runs reuse the saved token."""
    if token_saved():
        return False
    address = applicant_email() if email is None else email
    instructions = file_login_instructions(address, project_id)
    client_id, client_secret = client_credentials(email=address, project_id=project_id)
    print(instructions, flush=True)
    print('Sign in as ' + address + ' and choose Allow.', flush=True)
    credentials = browser_credentials(client_id, client_secret)
    save_user_token(credentials)
    return True


def main():
    created = ensure_file_login()
    print('Saved the drive.file refresh token.' if created else 'drive.file login already saved.')
    print('Deploy to publish it: ./deployment/ctl.sh up')
    return 0
