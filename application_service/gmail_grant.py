"""Gmail grant stored for the Application Service.

The one-hour access token is refreshed from the stored refresh token.
A Testing-mode refresh token still ends after about seven days. This module
mails the applicant before that and accepts the replacement grant.
"""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from html import escape

from application_service.google_auth import GoogleAccessToken

RESOURCE_KEY = 'gmail_oauth'
READONLY = 'https://www.googleapis.com/auth/gmail.readonly'
SEND = 'https://www.googleapis.com/auth/gmail.send'
SCOPES = READONLY + ' ' + SEND
NOTICE_WINDOW = 2 * 24 * 3600
TESTING_LIFETIME = 7 * 24 * 3600


def seed_from_environment(record=None, environ=None, now=None):
    """Fill an empty database record from the deployment environment."""
    record = dict(record or {})
    environ = os.environ if environ is None else environ
    if record.get('refresh_token'):
        return record
    refresh = environ.get('GOOGLE_GMAIL_REFRESH_TOKEN')
    client_id = environ.get('GOOGLE_GMAIL_CLIENT_ID')
    client_secret = environ.get('GOOGLE_GMAIL_CLIENT_SECRET')
    if not (refresh and client_id and client_secret):
        return record
    record.update({
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh,
        'scope': environ.get('GOOGLE_GMAIL_SCOPE') or SCOPES,
        'notice_email': environ.get('GOOGLE_GMAIL_NOTICE_EMAIL') or record.get('notice_email'),
        'obtained_at': int(now if now is not None else time.time()),
    })
    expires = environ.get('GOOGLE_GMAIL_REFRESH_EXPIRES_AT')
    if expires:
        record['refresh_expires_at'] = int(expires)
    elif 'refresh_expires_at' not in record:
        record['refresh_expires_at'] = record['obtained_at'] + TESTING_LIFETIME
    return record


def apply_refresh_token(record, refresh_token, now=None, expires_in=None, client_id=None, client_secret=None):
    """Replace the grant. Drops the previous renewal link."""
    if not refresh_token or not str(refresh_token).strip():
        raise ValueError('refresh_token is required')
    updated = dict(record or {})
    if client_id:
        updated['client_id'] = client_id.strip()
    if client_secret:
        updated['client_secret'] = client_secret.strip()
    if not updated.get('client_id') or not updated.get('client_secret'):
        raise ValueError('client_id and client_secret are required')
    moment = int(now if now is not None else time.time())
    updated['refresh_token'] = str(refresh_token).strip()
    updated['obtained_at'] = moment
    updated['refresh_expires_at'] = moment + int(expires_in if expires_in else TESTING_LIFETIME)
    updated['scope'] = updated.get('scope') or SCOPES
    for key in ('notice_code', 'notice_for', 'notice_error', 'code_verifier'):
        updated.pop(key, None)
    return updated


def remember_refresh_lifetime(record, token_response, now=None):
    """Keep Google's remaining refresh-token lifetime when the response includes it."""
    updated = dict(record)
    raw = (token_response or {}).get('refresh_token_expires_in')
    if raw:
        updated['refresh_expires_at'] = int(now if now is not None else time.time()) + int(raw)
    return updated


def notice_due(record, now=None, window=NOTICE_WINDOW):
    expires = record.get('refresh_expires_at')
    if not expires or not record.get('refresh_token'):
        return False
    moment = int(now if now is not None else time.time())
    if int(expires) - moment > window:
        return False
    return record.get('notice_for') != expires


def public_urls(base, code):
    root = base.rstrip('/')
    query = urllib.parse.urlencode({'code': code})
    return {
        'connect_url': root + '/gmail/connect?' + query,
        'renew_url': root + '/gmail/renew?' + query,
        'callback_url': root + '/gmail/callback',
    }


def prepare_notice(record, base, now=None):
    """Attach a one-time renewal code when the grant is inside the warning window."""
    updated = dict(record)
    if not notice_due(updated, now):
        return updated, None
    code = secrets.token_urlsafe(24)
    updated['notice_code'] = code
    urls = public_urls(base, code)
    expires = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(int(updated['refresh_expires_at'])))
    body = (
        'Your Gmail grant for the job search system expires at ' + expires + '.\n\n'
        'The service refreshes its own one-hour access token until then. '
        'It cannot extend this Google grant by itself.\n\n'
        'Approve a new grant and save it automatically:\n' + urls['connect_url'] + '\n\n'
        'If Google cannot return to the system, paste the new refresh token here:\n'
        + urls['renew_url'] + '\n'
    )
    return updated, {
        'to': updated.get('notice_email'),
        'subject': 'Gmail access for job search expires ' + expires,
        'body': body,
        **urls,
    }


def exchange_code(client_id, client_secret, code, redirect_uri, opener=None, verifier=None):
    opener = opener or urllib.request.urlopen
    fields = {
        'client_id': client_id, 'client_secret': client_secret, 'code': code,
        'redirect_uri': redirect_uri, 'grant_type': 'authorization_code',
    }
    if verifier:
        fields['code_verifier'] = verifier
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request('https://oauth2.googleapis.com/token', data=data, method='POST')
    with opener(request, timeout=30) as response:
        tokens = json.loads(response.read().decode())
    if not tokens.get('refresh_token'):
        raise ValueError('Google did not return a refresh token. Approve the consent screen again.')
    return tokens


def authorization_url(client_id, redirect_uri, state, challenge=None):
    params = {
        'client_id': client_id, 'redirect_uri': redirect_uri, 'response_type': 'code',
        'scope': SCOPES, 'access_type': 'offline', 'prompt': 'consent', 'state': state,
    }
    if challenge:
        params['code_challenge'] = challenge
        params['code_challenge_method'] = 'S256'
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode(params)


def start_connect(record, redirect_uri):
    """Remember a PKCE verifier so the callback can finish the grant."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
    updated = dict(record)
    updated['code_verifier'] = verifier
    return updated, authorization_url(updated['client_id'], redirect_uri, updated['notice_code'], challenge)


def send_notice(access_token, message, opener=None):
    if not message.get('to'):
        raise ValueError('notice_email is missing')
    opener = opener or urllib.request.urlopen
    raw = 'To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}'.format(**message)
    encoded = base64.urlsafe_b64encode(raw.encode()).decode()
    request = urllib.request.Request(
        'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
        data=json.dumps({'raw': encoded}).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + access_token, 'Content-Type': 'application/json'})
    with opener(request, timeout=30) as response:
        return json.loads(response.read().decode())


def refresh_access(record, opener=None, now=None):
    token = GoogleAccessToken(record['refresh_token'], record['client_id'], record['client_secret'], opener=opener)
    # Read the refresh response lifetime by one direct call so Testing expiry stays current.
    opener = opener or urllib.request.urlopen
    payload = urllib.parse.urlencode({
        'client_id': record['client_id'], 'client_secret': record['client_secret'],
        'refresh_token': record['refresh_token'], 'grant_type': 'refresh_token',
    }).encode()
    request = urllib.request.Request('https://oauth2.googleapis.com/token', data=payload, method='POST')
    with opener(request, timeout=30) as response:
        result = json.loads(response.read().decode())
    if not result.get('access_token'):
        raise RuntimeError('Google token refresh response did not include access_token')
    token._access_token = result['access_token']
    token._expires_at = time.time() + int(result.get('expires_in', 3600))
    return token, remember_refresh_lifetime(record, result, now)


def run_maintenance(record, base, opener=None, now=None):
    """Refresh the access token and mail one warning per expiry."""
    updated = seed_from_environment(record, now=now)
    if not updated.get('refresh_token'):
        return updated, {'status': 'missing'}
    try:
        access, updated = refresh_access(updated, opener=opener, now=now)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:200]
        return updated, {'status': 'refresh_failed', 'detail': detail}
    prepared, message = prepare_notice(updated, base, now)
    if message is None:
        return prepared, {'status': 'current', 'refresh_expires_at': prepared.get('refresh_expires_at')}
    try:
        send_notice(access.token(), message, opener=opener)
    except (urllib.error.URLError, ValueError, RuntimeError) as exc:
        prepared['notice_error'] = str(exc)[:300]
        report = {'status': 'notice_failed', 'renew_url': message['renew_url'], 'detail': prepared['notice_error']}
        return prepared, report
    prepared['notice_for'] = prepared['refresh_expires_at']
    prepared.pop('notice_error', None)
    return prepared, {
        'status': 'notice_sent',
        'connect_url': message['connect_url'],
        'renew_url': message['renew_url'],
        'refresh_expires_at': prepared['refresh_expires_at'],
    }


def code_matches(record, code):
    saved = record.get('notice_code')
    return bool(saved and code and secrets.compare_digest(str(saved), str(code)))


def renew_page(record, code, callback_url):
    if not code_matches(record, code):
        return 403, _page('This renewal link is not valid.')
    form = '''<p>Approve Gmail again. The system saves the new grant when Google returns here.</p>
<p><a href="/gmail/connect?code={code}">Get a new Gmail grant</a></p>
<form method="post" action="/gmail/renew">
<input type="hidden" name="code" value="{code}">
<label>Refresh token <input name="refresh_token" required></label>
<label>Client id <input name="client_id" placeholder="leave blank to keep the saved client"></label>
<label>Client secret <input name="client_secret" placeholder="leave blank to keep the saved client"></label>
<button type="submit">Save grant</button>
</form>'''.format(code=escape(code))
    return 200, _page(form)


def _page(body):
    html = '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Gmail grant</title></head><body>' + body + '</body></html>'
    return html.encode()


def load_record():
    from .database import connect
    from .initialization import resource
    try:
        with connect() as connection:
            saved = resource(connection, RESOURCE_KEY) or {}
    except RuntimeError:
        saved = {}
    return seed_from_environment(saved)


def save_record(record):
    from .database import connect
    from .initialization import save_resource
    with connect() as connection:
        save_resource(connection, RESOURCE_KEY, record)


def access_from_store(opener=None):
    """Return a Gmail access token from the database or deployment environment."""
    record = load_record()
    if not record.get('refresh_token'):
        return None
    if READONLY not in (record.get('scope') or SCOPES).split():
        return None
    return GoogleAccessToken(record['refresh_token'], record['client_id'], record['client_secret'], opener=opener)


def mutate_record(change):
    """Read and update one grant atomically, including concurrent OAuth saves."""
    from .database import connect, installation_lock
    from .initialization import save_resource
    with connect() as connection, installation_lock(connection, 'gmail-grant'):
        with connection.transaction():
            row = connection.execute(
                'SELECT data FROM installation_resources WHERE resource_key=%s FOR UPDATE',
                (RESOURCE_KEY,)).fetchone()
            record = seed_from_environment(row[0] if row else {})
            updated, result = change(record)
            if updated is not None:
                save_resource(connection, RESOURCE_KEY, updated)
            return result


def install_grant(data):
    """Install an absent grant through the same domain operation for REST/bulk."""
    def change(current):
        if current.get('refresh_token'):
            return None, {'status': 'present'}
        updated = apply_refresh_token(current, data['refresh_token'], expires_in=data.get('expires_in'),
                                      client_id=data['client_id'], client_secret=data['client_secret'])
        for key in ('notice_email', 'scope'):
            if data.get(key):
                updated[key] = data[key]
        return updated, {'status': 'installed'}
    return mutate_record(change)


def acknowledge_notice(expected_expiry=None):
    """An old notice cannot acknowledge a newly renewed grant."""
    def change(record):
        if expected_expiry is not None and record.get('refresh_expires_at') != expected_expiry:
            from .bulk_updates import Conflict
            raise Conflict('Grant changed after notice preparation')
        if record.get('refresh_expires_at') is not None:
            record['notice_for'] = record['refresh_expires_at']
        record.pop('notice_error', None)
        return record, {'status': 'notice_sent', 'refresh_expires_at': record.get('refresh_expires_at')}
    return mutate_record(change)
