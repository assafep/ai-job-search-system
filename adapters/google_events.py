"""Read-only Gmail/Calendar event sources; no application-state writes."""

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape

from adapters.google.auth import access_token_value, token_from_environment


SCOPES = {
    'gmail': 'https://www.googleapis.com/auth/gmail.readonly',
    'calendar': 'https://www.googleapis.com/auth/calendar.events.readonly',
}


class GoogleEventError(RuntimeError):
    def __init__(self, source, status=None, reason='unavailable'):
        self.source, self.status, self.reason = source, status, reason
        super().__init__('{} event source unavailable: HTTP {}, {}'.format(source, status, reason))


def body_text(payload):
    """Decode inline message bodies only, never attachments or remote HTML."""
    plain, html = [], []
    def visit(part):
        encoded = (part.get('body') or {}).get('data')
        if encoded and not part.get('filename'):
            decoded = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode('utf-8', 'replace')
            if part.get('mimeType') == 'text/plain':
                plain.append(decoded)
            elif part.get('mimeType') == 'text/html':
                html.append(unescape(re.sub('<[^>]+>', ' ', decoded)))
        for child in part.get('parts') or []:
            visit(child)
    visit(payload)
    return '\n'.join(plain or html)[:30000]


_ENV_REFRESH = {
    'gmail': 'GOOGLE_GMAIL_REFRESH_TOKEN',
    'calendar': 'GOOGLE_CALENDAR_REFRESH_TOKEN',
}


def saved_scope_access(kind, path=None, opener=None):
    """Refresh a read-only Gmail or Calendar token, or return None."""
    from pathlib import Path
    from adapters.google.auth import GoogleAccessToken
    token_path = path or Path(__file__).resolve().parents[1] / '.onboarding' / (kind + '-user.json')
    saved = None
    if token_path.is_file():
        try:
            saved = json.loads(token_path.read_text())
        except (OSError, ValueError):
            saved = None
    if isinstance(saved, dict) and SCOPES[kind] in str(saved.get('scope') or '').split():
        fields = [saved.get('refresh_token'), saved.get('client_id'), saved.get('client_secret')]
        if all(fields):
            return GoogleAccessToken(*fields, opener=opener)
    refresh = (os.environ.get(_ENV_REFRESH[kind]) or '').strip()
    client_id = (os.environ.get('GOOGLE_CLIENT_ID') or '').strip()
    client_secret = (os.environ.get('GOOGLE_CLIENT_SECRET') or '').strip()
    if refresh and client_id and client_secret:
        return GoogleAccessToken(refresh, client_id, client_secret, opener=opener)
    return None


def saved_gmail_access(path=None, opener=None):
    """Refresh the onboarding Gmail token, or return None."""
    return saved_scope_access('gmail', path, opener)


def saved_calendar_access(path=None, opener=None):
    """Refresh the onboarding Calendar token, or return None."""
    return saved_scope_access('calendar', path, opener)


class GoogleEventSource:
    def __init__(self, access_token=None, opener=None, now=None):
        self._explicit_token = access_token is not None
        self.access_token = access_token if access_token is not None else token_from_environment()
        self.opener = opener or urllib.request.urlopen
        self.now = now or datetime.now(timezone.utc)

    def _get(self, source, path, params=None):
        token_source = self.access_token
        if source in SCOPES and not self._explicit_token:
            dedicated = saved_scope_access(source, opener=self.opener)
            if dedicated is not None:
                token_source = dedicated
        token = access_token_value(token_source)
        if not token:
            raise GoogleEventError(source, reason='missing_credentials')
        base = {'gmail': 'https://gmail.googleapis.com/gmail/v1/users/me',
                'calendar': 'https://www.googleapis.com/calendar/v3/calendars/primary'}[source]
        url = base + path + ('?' + urllib.parse.urlencode(params) if params else '')
        request = urllib.request.Request(url, method='GET', headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
        try:
            with self.opener(request, timeout=20) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as error:
            try:
                details = json.loads(error.read().decode('utf-8'))['error']
                reasons = details.get('errors') or []
                reason = reasons[0].get('reason', 'upstream_error') if reasons else 'upstream_error'
            except (ValueError, KeyError, TypeError):
                reason = 'upstream_error'
            raise GoogleEventError(source, error.code, reason)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            # Do not expose tokens, session URLs, response bodies, or mail.
            raise GoogleEventError(source, reason=type(error).__name__)

    def gmail(self, companies, limit=100):
        if not 1 <= limit <= 500:
            raise ValueError('Gmail poll limit must be between 1 and 500')
        names = sorted({re.sub(r'["\\{}]', ' ', name).strip() for name in companies if name.strip()})
        if not names:
            return [], False
        query = 'newer_than:14d -in:sent -in:trash -in:spam {' + ' '.join('"' + name + '"' for name in names) + '}'
        messages, page_token = [], None
        while len(messages) < limit:
            params = {'q': query, 'maxResults': min(100, limit - len(messages))}
            if page_token:
                params['pageToken'] = page_token
            result = self._get('gmail', '/messages', params)
            for item in result.get('messages') or []:
                raw = self._get('gmail', '/messages/' + urllib.parse.quote(item['id'], safe=''), {'format': 'full'})
                if set(raw.get('labelIds') or []) & {'SENT', 'DRAFT', 'SPAM', 'TRASH'}:
                    continue
                headers = {h['name'].lower(): h.get('value', '') for h in (raw.get('payload') or {}).get('headers', [])}
                messages.append({'message_id': raw['id'], 'thread_id': raw.get('threadId', raw['id']),
                                 'sender': headers.get('from', ''), 'subject': headers.get('subject', ''),
                                 'body': body_text(raw.get('payload') or {})})
            page_token = result.get('nextPageToken')
            if not page_token:
                break
        return messages, bool(page_token)

    def calendar(self, limit=250):
        if not 1 <= limit <= 2500:
            raise ValueError('Calendar poll limit must be between 1 and 2500')
        events, page_token = [], None
        while len(events) < limit:
            params = {'timeMin': self.now.isoformat(), 'timeMax': (self.now + timedelta(days=30)).isoformat(),
                      'singleEvents': 'true', 'orderBy': 'startTime', 'showDeleted': 'false',
                      'maxResults': min(250, limit - len(events))}
            if page_token:
                params['pageToken'] = page_token
            result = self._get('calendar', '/events', params)
            for raw in result.get('items') or []:
                if raw.get('status') == 'cancelled':
                    continue
                events.append({'event_id': raw['id'] + ':' + raw.get('updated', 'initial'), 'meeting_id': raw['id'],
                               'title': raw.get('summary', ''), 'description': raw.get('description', ''),
                               'start': (raw.get('start') or {}).get('dateTime') or (raw.get('start') or {}).get('date'),
                               'end': (raw.get('end') or {}).get('dateTime') or (raw.get('end') or {}).get('date'),
                               'interviewer': None})
            page_token = result.get('nextPageToken')
            if not page_token:
                break
        return events, bool(page_token)
