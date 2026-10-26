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


class GoogleConsentRequired(ProvisioningFailure):
    """The owner must configure/authorize their dedicated Google OAuth client."""



def file_login_instructions(email, project_id=None):
    """Steps for the person being onboarded. The test user is their email."""
    if not email:
        raise ProvisioningFailure(
            'The applicant profile has no personal.email. Add that address, then rerun Drive setup.')
    query = '?project=' + project_id if project_id else ''
    credentials = 'https://console.cloud.google.com/apis/credentials' + query
    return '\n'.join((
        'Setup paused: the downloaded Desktop OAuth client JSON is missing.',
        'Expected file: ' + str(CLIENT_PATH),
        'No consent browser will open until this file is saved at that path.',
        '1. Open Credentials: ' + credentials,
        ('   Select project ' + project_id + '; make sure its ID matches the Google Cloud project ID printed earlier in the log.'
         if project_id else '   Select the project matching the Google Cloud project ID printed earlier in the log.'),
        '2. Choose Create credentials > OAuth client ID and select Desktop app.',
        '   Recommended name: AJSS Application Service.',
        '   Mark the checkbox "Use this client for an AI-powered agent".',
        '   Save/create the client and download its JSON (containing the client ID and client secret).',
        '   Save the JSON as .onboarding/oauth-client.json in this repository.',
        '3. Notice the existing "AJSS Drive writer" service account (ID: drive-writer).',
        '   Do not delete it; you will need it soon.',
        '4. After saving the JSON at the expected path, rerun ./deployment/ctl.sh up.',
        '   The script will display a Google consent link and try to open it in your host browser.',
        '   Use your existing Google session for ' + email + ' and approve AJSS file access.',
        '   The permission is called drive.file; it is not a web page to open.',
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
        raise GoogleConsentRequired(file_login_instructions(email, project_id))
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
    client_id, client_secret = client_credentials(email=address, project_id=project_id)
    print('OAuth client JSON found. Requesting AJSS Drive file-access approval for ' + address + '.', flush=True)
    print('Google shows the application name configured for your OAuth client (recommended: "AJSS Application Service").', flush=True)
    print('You can use your existing Google sign-in; approve this application when prompted.', flush=True)
    credentials = browser_credentials(client_id, client_secret)
    save_user_token(credentials)
    return True


def main():
    created = ensure_file_login()
    print('Saved the drive.file refresh token.' if created else 'drive.file login already saved.')
    print('Deploy to publish it: ./deployment/ctl.sh up')
    return 0
