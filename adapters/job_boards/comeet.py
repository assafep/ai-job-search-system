"""Read-only Comeet careers adapter for Israeli employer boards.

Comeet publishes each company's careers token in the public board HTML. That
token only reads published positions. time_updated is an edit timestamp, so it
is stored as updated_at and is never treated as the publication date.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .interface import EmployerJobBoard
from .parsing import text as _text
from datetime import datetime, timedelta, timezone

from .parsing import plain_text as _plain_text


API_URL = 'https://www.comeet.co/careers-api/2.0/company/{}/positions?{}'
PAGE_URL = 'https://www.comeet.com/jobs/{}'
# Comeet has no publication date. An edit older than this is not evidence the
# advertisement is still being maintained, so it stays out of discovery.
EDIT_WINDOW = timedelta(days=60)
_JUNIOR = re.compile(r'\b(?:intern|internship|junior|student)\b', re.I)
_COUNTRY = {'ISR': 'IL', 'ISRAEL': 'IL', 'USA': 'US', 'GBR': 'GB', 'UK': 'GB'}


def _country_code(value):
    text = (value or '').strip()
    if not text:
        return ''
    upper = text.upper()
    if upper in _COUNTRY:
        return _COUNTRY[upper]
    if len(upper) == 2 and upper.isalpha():
        return upper
    return ''


class ComeetJobBoard(EmployerJobBoard):
    provider_name = 'comeet'
    hosts = ('www.comeet.com', 'comeet.com', 'www.comeet.co')

    def discovery_pages(self, context):
        for token, company in (context.boards or self.boards).items():
            yield _board_identity(token)[1], company

    def fetch_details_scrape(self, context, transport):
        from .parsing import description
        source = transport.read_text(context.posting_url)
        content = max(_extract_comeet(source), description(source, transport.description_reader), key=len)
        return transport.details(context.posting_url, context.posting_url, content)

    def __init__(self, boards=None, opener=None, timeout=30):
        if boards is not None and (not isinstance(boards, dict) or not boards):
            raise ValueError('at least one Comeet board is required')
        self.boards = boards or {}
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def list_jobs(self, *, boards=None, _transport=None):
        postings = []
        for key, company in (boards if boards is not None else self.boards).items():
            try:
                uid, page = _board_identity(key)
                token = self._token(key, uid, page, _transport)
                for job in self._positions(key, uid, token, _transport):
                    if job.get('is_internal') is True:
                        continue
                    level = job.get('experience_level') or ''
                    if level and _JUNIOR.search(level):
                        continue
                    if not _edit_is_current(job.get('time_updated')):
                        continue
                    posting = self._normalize(company, job)
                    if posting['posting_url']:
                        postings.append(posting)
            finally:
                if _transport is None and self.opener is urllib.request.urlopen:
                    time.sleep(0.35)
        return postings

    def _token(self, key, uid, page, _transport=None):
        html = self._read(page, 'text/html', _transport)
        match = re.search(
            r'"company_uid"\s*:\s*"' + re.escape(uid) + r'"\s*,\s*"token"\s*:\s*"([A-Fa-f0-9]+)"',
            html)
        if match is None:
            raise RuntimeError('Comeet board {} did not publish a careers token'.format(key))
        return match.group(1)

    def _positions(self, key, uid, token, _transport=None):
        query = urllib.parse.urlencode({'token': token, 'details': 'true'})
        body = self._read(API_URL.format(urllib.parse.quote(uid, safe='.-'), query), 'application/json', _transport)
        try:
            payload = json.loads(body)
        except ValueError as error:
            raise RuntimeError('Comeet board {} returned an invalid response'.format(key)) from error
        if not isinstance(payload, list):
            raise RuntimeError('Comeet board {} returned an invalid response'.format(key))
        return payload

    def _read(self, url, accept, _transport=None):
        request = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; ai-job-search-system/1.0)',
            'Accept': accept,
            'Accept-Language': 'en-US,en;q=0.9',
        })
        try:
            response_context = self._open(request, _transport)
            with response_context as response:
                return response.read().decode('utf-8')
        except urllib.error.HTTPError as error:
            raise RuntimeError('HTTP {}'.format(error.code)) from error
        except urllib.error.URLError as error:
            raise RuntimeError('unavailable: {}'.format(error.reason)) from error

    @staticmethod
    def _normalize(company, job):
        location = job.get('location') if isinstance(job.get('location'), dict) else {}
        code = _country_code(location.get('country') or location.get('name'))
        city = location.get('city') or ''
        label = location.get('name') or ''
        parts = []
        for detail in job.get('details') or []:
            if isinstance(detail, dict) and detail.get('value'):
                parts.append(_plain_text(detail['value']))
        return {
            'company': job.get('company_name') or company,
            'role': job.get('name') or '',
            'posting_url': (job.get('url_comeet_hosted_page')
                            or job.get('url_recruit_hosted_page')
                            or job.get('url_active_page')
                            or ''),
            'location': ', '.join(item for item in (city or label, location.get('country') or '') if item),
            'countries': [code] if code else [],
            'description': '\n'.join(parts),
            'departments': [job['department']] if job.get('department') else [],
            'source': 'comeet',
            'source_id': job.get('uid') or '',
            'updated_at': job.get('time_updated'),
            'posted_at': None,
            'workplace_type': job.get('workplace_type') or '',
            'employment_type': job.get('employment_type') or '',
        }


def _edit_is_current(value, now=None):
    if not isinstance(value, str) or not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return now - EDIT_WINDOW <= parsed <= now + timedelta(days=1)


def _board_identity(key):
    if '/' in key:
        slug, uid = key.split('/', 1)
        page = PAGE_URL.format('/'.join((
            urllib.parse.quote(slug, safe='.-_'),
            urllib.parse.quote(uid, safe='.-_'))))
        return uid, page
    uid = urllib.parse.quote(key, safe='.-_')
    return key, PAGE_URL.format(uid)


def _extract_comeet(source):
    """Extract Comeet's serialized POSITION_DATA object without a browser."""
    marker = 'POSITION_DATA = '
    start = source.find(marker)
    if start < 0:
        return ''
    try:
        payload, _ = json.JSONDecoder().raw_decode(source[start + len(marker):])
    except (TypeError, ValueError):
        return ''
    if not isinstance(payload, dict):
        return ''
    details = ((payload.get('custom_fields') or {}).get('details') or [])
    parts = []
    for item in details:
        if not isinstance(item, dict):
            continue
        name, value = item.get('name'), item.get('value')
        if name and value:
            parts.append('{}\n{}'.format(name, _text(str(value))))
    location = payload.get('location') or {}
    location_text = ', '.join(str(location.get(key)) for key in ('name', 'city', 'country') if location.get(key))
    if location_text:
        parts.append('Location\n' + location_text)
    return '\n\n'.join(part for part in parts if part.strip())
