"""Read-only Himalayas search for roles restricted to a country.

Worldwide remote listings are not treated as eligible for that country.
posting_url is the Himalayas application link from the public jobs API.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .interface import JobBoardAdapter, UnsupportedOperation
from .parsing import text as _text
from datetime import datetime, timezone

from .parsing import plain_text as _plain_text


BASE_URL = 'https://himalayas.app/jobs/api/search'
# A long allow-list is a worldwide remote role that happens to name Israel.
MAX_COUNTRY_RESTRICTIONS = 6
_ISRAEL = re.compile(r'\bisrael\b', re.I)


def _iso_timestamp(value):
    if isinstance(value, str) and value:
        return value
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    seconds = value / 1000.0 if value > 10000000000 else float(value)
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (OverflowError, OSError, ValueError):
        return None


def _restrictions(values):
    labels = []
    countries = set()
    for item in values or []:
        if isinstance(item, dict):
            code = (item.get('alpha2') or '').upper()
            name = item.get('name') or item.get('slug') or ''
        else:
            code = ''
            name = str(item or '')
        if name:
            labels.append(name)
        elif code:
            labels.append(code)
        if code == 'IL' or _ISRAEL.search(name):
            countries.add('IL')
        elif len(code) == 2 and code.isalpha():
            countries.add(code)
    return labels, sorted(countries)


class HimalayasJobBoard(JobBoardAdapter):
    provider_name = 'himalayas'
    hosts = ('himalayas.app',)

    def search_api(self, context, transport):
        return self.search(context.query, country=next(iter(context.countries), 'IL'),
                           max_pages=context.max_pages, _transport=transport)

    def discovery_pages(self, context):
        yield 'https://himalayas.app/jobs?' + urllib.parse.urlencode({'q': context.query}), ''

    def fetch_details_api(self, context, transport):
        url = _himalayas_endpoint(context.posting_url)
        if not url:
            raise UnsupportedOperation('Not an exact Himalayas posting URL')
        source = transport.read_text(url, {'Accept': 'application/json'})
        return transport.details(context.posting_url, url, _extract_himalayas(source, context.posting_url))

    def __init__(self, opener=None, timeout=30):
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def search(self, query, country='IL', max_pages=3, *, _transport=None):
        if max_pages < 1:
            raise ValueError('max_pages must be at least 1')
        found = {}
        for page in range(1, max_pages + 1):
            params = {
                'q': query,
                'country': country,
                'exclude_worldwide': 'true',
                'sort': 'recent',
                'page': page,
            }
            payload = self._read(BASE_URL + '?' + urllib.parse.urlencode(params), _transport)
            jobs = payload.get('jobs') if isinstance(payload, dict) else None
            if not isinstance(jobs, list):
                raise RuntimeError('Himalayas returned an invalid response')
            for job in jobs:
                posting = self._normalize(job)
                if posting and posting['posting_url']:
                    found[posting['posting_url']] = posting
            if len(jobs) < 20:
                break
        return list(found.values())

    def _read(self, url, _transport=None):
        request = urllib.request.Request(url, headers={
            'Accept': 'application/json',
            'User-Agent': 'ai-job-search-system/1.0',
        })
        try:
            response_context = self._open(request, _transport)
            with response_context as response:
                payload = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as error:
            raise RuntimeError('Himalayas returned HTTP {}'.format(error.code)) from error
        except urllib.error.URLError as error:
            raise RuntimeError('Himalayas unavailable: {}'.format(error.reason)) from error
        except ValueError as error:
            raise RuntimeError('Himalayas returned an invalid response') from error
        return payload

    @staticmethod
    def _normalize(job):
        labels, countries = _restrictions(job.get('locationRestrictions'))
        if len(labels) > MAX_COUNTRY_RESTRICTIONS:
            return None
        seniority = job.get('seniority') or []
        if isinstance(seniority, str):
            seniority = [seniority]
        return {
            'company': job.get('companyName') or '',
            'role': job.get('title') or '',
            'posting_url': job.get('applicationLink') or '',
            'location': '; '.join(labels),
            'countries': countries,
            'description': _plain_text(job.get('description') or job.get('excerpt') or ''),
            'source': 'himalayas',
            'source_id': job.get('guid') or '',
            'posted_at': _iso_timestamp(job.get('pubDate')),
            'publication_date_kind': 'first_published',
            'employment_type': job.get('employmentType') or '',
            'seniority': seniority,
        }


def _himalayas_endpoint(posting_url):
    """Map a public posting to Himalayas' documented exact-job API."""
    parsed = urllib.parse.urlsplit(posting_url)
    if (parsed.scheme != 'https' or parsed.netloc.lower() != 'himalayas.app'
            or not re.fullmatch(r'/companies/[^/]+/jobs/[^/]+/?', parsed.path)):
        return None
    return 'https://himalayas.app/jobs/api' + parsed.path.rstrip('/')


def _extract_himalayas(source, posting_url):
    payload = json.loads(source)
    job = payload.get('job') if isinstance(payload, dict) else None
    if not isinstance(job, dict):
        raise ValueError('Himalayas API response has no job object')
    parsed = urllib.parse.urlsplit(posting_url)
    expected = 'https://himalayas.app' + parsed.path.rstrip('/')
    guid = job.get('guid')
    company = parsed.path.split('/')[2]
    if (not isinstance(guid, str) or guid.rstrip('/') != expected
            or job.get('companySlug') != company):
        raise ValueError('Himalayas API returned a different posting')
    description = job.get('description')
    if not isinstance(description, str):
        raise ValueError('Himalayas API description must be HTML text')
    return _text(description)
