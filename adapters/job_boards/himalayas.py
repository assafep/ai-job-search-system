"""Read-only Himalayas search for roles restricted to a country.

Worldwide remote listings are not treated as eligible for that country.
posting_url is the Himalayas application link from the public jobs API.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .greenhouse import _plain_text


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


class HimalayasJobBoard:
    def __init__(self, opener=None, timeout=30):
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def search(self, query, country='IL', max_pages=3):
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
            payload = self._read(BASE_URL + '?' + urllib.parse.urlencode(params))
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

    def _read(self, url):
        request = urllib.request.Request(url, headers={
            'Accept': 'application/json',
            'User-Agent': 'ai-job-search-system/1.0',
        })
        try:
            response_context = (self.opener(request, timeout=self.timeout)
                                if self.opener is urllib.request.urlopen else self.opener(request))
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
