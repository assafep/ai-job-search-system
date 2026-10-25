"""Google login for reading Gmail and mailing one renewal notice.

gmail.readonly reads inbound mail. gmail.send is used only to mail the
applicant when that grant is about to expire. The token cannot delete mail.
"""
import json
import time
from pathlib import Path

from .drive_login import applicant_email
from .oauth_redirect import LOOPBACK, ensure_callback
from .providers.google import browser_credentials
from .state import ProvisioningFailure

REPO = Path(__file__).resolve().parents[1]
TOKEN_PATH = REPO / '.onboarding' / 'gmail-user.json'
GMAIL_READONLY = 'https://www.googleapis.com/auth/gmail.readonly'
GMAIL_SEND = 'https://www.googleapis.com/auth/gmail.send'
GMAIL_SCOPES = (GMAIL_READONLY, GMAIL_SEND)
PRODUCTION_CALLBACK = 'https://ajss-one.vercel.app/gmail/callback'
LOOPBACK_PORT = int(LOOPBACK.rsplit(':', 1)[1].strip('/'))


def save_gmail_token(credentials, path=TOKEN_PATH, email=None, now=None):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    record = {
        'client_id': credentials['GOOGLE_CLIENT_ID'],
        'client_secret': credentials['GOOGLE_CLIENT_SECRET'],
        'refresh_token': credentials['GOOGLE_REFRESH_TOKEN'],
        'scope': ' '.join(GMAIL_SCOPES),
    }
    if email:
        record['notice_email'] = email
    expires_in = credentials.get('GOOGLE_REFRESH_EXPIRES_IN')
    if expires_in:
        record['refresh_expires_at'] = int(now if now is not None else time.time()) + int(expires_in)
    temporary.write_text(json.dumps(record))
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def gmail_token_saved(path=TOKEN_PATH):
    if not path.is_file():
        return False
    saved = json.loads(path.read_text())
    scopes = set((saved.get('scope') or '').split())
    return GMAIL_READONLY in scopes and all(
        saved.get(key) for key in ('client_id', 'client_secret', 'refresh_token'))


def load_gmail_token(path=TOKEN_PATH):
    """Return refresh credentials, or None when onboarding has not saved them."""
    if not gmail_token_saved(path):
        return None
    saved = json.loads(path.read_text())
    return {
        'client_id': saved['client_id'],
        'client_secret': saved['client_secret'],
        'refresh_token': saved['refresh_token'],
        'refresh_expires_at': saved.get('refresh_expires_at'),
        'notice_email': saved.get('notice_email'),
        'scope': saved.get('scope'),
    }


def ensure_gmail_login(email=None, project_id=None, path=TOKEN_PATH):
    """Ask for gmail.readonly once. Later onboarding runs reuse the saved token."""
    if gmail_token_saved(path):
        return False
    address = applicant_email() if email is None else email
    if not address:
        raise ProvisioningFailure(
            'The applicant profile has no personal.email. Add that address, then rerun onboarding.')
    client_id, client_secret = ensure_callback(project_id=project_id)
    print('Google needs Gmail access for ' + address + '.', flush=True)
    print('Allow read-only mail plus one renewal email to yourself. This grant cannot delete mail.', flush=True)
    credentials = browser_credentials(
        client_id, client_secret, scopes=GMAIL_SCOPES, purpose='Gmail read and renewal mail', port=LOOPBACK_PORT)
    save_gmail_token(credentials, path, email=address)
    return True
