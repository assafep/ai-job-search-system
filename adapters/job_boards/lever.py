"""Read-only Lever postings adapter.

createdAt is when Lever created the posting, so it is a publication time.
The EU host is used only when the global host has no such board.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from .interface import EmployerJobBoard, UnsupportedOperation
from datetime import datetime, timezone

from .parsing import plain_text as _plain_text


BASES = (
    'https://api.lever.co/v0/postings/{}?mode=json',
    'https://api.eu.lever.co/v0/postings/{}?mode=json',
)
_COUNTRY = {'ISR': 'IL', 'ISRAEL': 'IL', 'USA': 'US', 'GBR': 'GB', 'UK': 'GB'}


def _country_code(value):
    text = (value or '').strip()
    upper = text.upper()
    if upper in _COUNTRY:
        return _COUNTRY[upper]
    if len(upper) == 2 and upper.isalpha():
        return upper
    return ''


def _iso_from_epoch(value):
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    seconds = value / 1000.0 if value > 10000000000 else float(value)
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (OverflowError, OSError, ValueError):
        return None


class LeverJobBoard(EmployerJobBoard):
    provider_name = 'lever'
    hosts = ('jobs.lever.co', 'jobs.eu.lever.co')

    def fetch_details_api(self, context, transport):
        parsed = urllib.parse.urlsplit(context.posting_url)
        parts = parsed.path.strip('/').split('/')
        if len(parts) != 2:
            raise UnsupportedOperation('Not an exact Lever posting URL')
        token, job_id = parts
        template = BASES[1] if parsed.hostname == 'jobs.eu.lever.co' else BASES[0]
        url = template.format(urllib.parse.quote(token, safe=''))
        jobs = transport.read_json(url)
        if not isinstance(jobs, list):
            raise ValueError('Lever returned an invalid response')
        for job in jobs:
            if str(job.get('id')) == job_id:
                description = self._normalize('', job)['description']
                additional = job.get('additionalPlain') or _plain_text(job.get('additional'))
                return transport.details(context.posting_url, url, '\n'.join(filter(None, [description, additional])))
        raise ValueError('Lever feed has no matching public posting')

    def discovery_pages(self, context):
        for token, company in (context.boards or self.boards).items():
            yield 'https://jobs.lever.co/' + urllib.parse.quote(token, safe=''), company

    def __init__(self, boards=None, opener=None, timeout=30):
        if boards is not None and (not isinstance(boards, dict) or not boards):
            raise ValueError('at least one Lever board is required')
        self.boards = boards or {}
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def list_jobs(self, *, boards=None, _transport=None):
        postings = []
        for token, company in (boards if boards is not None else self.boards).items():
            for job in self._postings(token, _transport):
                posting = self._normalize(company, job)
                if posting['posting_url']:
                    postings.append(posting)
        return postings

    def _postings(self, token, _transport=None):
        quoted = urllib.parse.quote(token, safe='')
        last_error = None
        for template in BASES:
            try:
                body = self._read(template.format(quoted), _transport)
            except RuntimeError as error:
                last_error = error
                if 'HTTP 404' in str(error):
                    continue
                raise RuntimeError('Lever board {} {}'.format(token, error)) from error
            try:
                payload = json.loads(body)
            except ValueError as error:
                raise RuntimeError('Lever board {} returned an invalid response'.format(token)) from error
            if not isinstance(payload, list):
                raise RuntimeError('Lever board {} returned an invalid response'.format(token))
            return payload
        raise RuntimeError('Lever board {} {}'.format(token, last_error or 'unavailable'))

    def _read(self, url, _transport=None):
        request = urllib.request.Request(url, headers={
            'Accept': 'application/json',
            'User-Agent': 'ai-job-search-system/1.0',
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
        categories = job.get('categories') or {}
        locations = []
        for item in [categories.get('location') or ''] + list(categories.get('allLocations') or []):
            if item and item not in locations:
                locations.append(item)
        countries = set()
        code = _country_code(job.get('country'))
        if code:
            countries.add(code)
        parts = [job.get('descriptionPlain') or '']
        for item in job.get('lists') or []:
            if not isinstance(item, dict):
                continue
            parts.append(item.get('text') or '')
            parts.append(_plain_text(item.get('content') or ''))
        return {
            'company': company,
            'role': job.get('text') or '',
            'posting_url': job.get('hostedUrl') or job.get('applyUrl') or '',
            'location': '; '.join(item for item in locations if item),
            'countries': sorted(countries),
            'description': '\n'.join(part for part in parts if part),
            'departments': [categories['team']] if categories.get('team') else [],
            'source': 'lever',
            'source_id': job.get('id') or '',
            'posted_at': _iso_from_epoch(job.get('createdAt')),
            'publication_date_kind': 'first_published',
            'workplace_type': job.get('workplaceType') or '',
        }
