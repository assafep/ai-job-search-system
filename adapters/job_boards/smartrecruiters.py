"""Read-only SmartRecruiters public postings adapter.

releasedDate is when the company released the posting, so it is a publication
time. The list call has no job text. Descriptions are read only for postings
whose list location is Israel.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from services.discovery_sources import ISRAEL_LOCATION

from .greenhouse import _plain_text


BASE_URL = 'https://api.smartrecruiters.com/v1/companies/{}/postings'
_PAGE_LIMIT = 5000


def _in_israel(job):
    location = job.get('location') or {}
    if not isinstance(location, dict):
        return False
    country = (location.get('country') or '').strip().upper()
    if country in {'IL', 'ISR'}:
        return True
    text = ' '.join(str(location.get(key) or '') for key in ('city', 'region', 'fullLocation'))
    return bool(ISRAEL_LOCATION.search(text))


class SmartRecruitersJobBoard:
    def __init__(self, boards, opener=None, timeout=30):
        if not isinstance(boards, dict) or not boards:
            raise ValueError('at least one SmartRecruiters company identifier is required')
        self.boards = boards
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def list_jobs(self):
        postings = []
        for token, company in self.boards.items():
            listed = [job for job in self._listed(token) if _in_israel(job)]
            failures = 0
            for job in listed:
                job_id = job.get('id')
                if not job_id:
                    failures += 1
                    continue
                try:
                    detail = self._json(self._detail_url(token, job_id), token)
                except RuntimeError:
                    failures += 1
                    continue
                if not isinstance(detail, dict):
                    failures += 1
                    continue
                posting = self._normalize(company, detail)
                if posting['posting_url']:
                    postings.append(posting)
                else:
                    failures += 1
            if listed and failures == len(listed):
                raise RuntimeError('SmartRecruiters board {} descriptions were unreadable'.format(token))
        return postings

    def _listed(self, token):
        quoted = urllib.parse.quote(token, safe='')
        offset = 0
        found = []
        total = None
        while total is None or offset < total:
            url = BASE_URL.format(quoted) + '?limit=100&offset={}'.format(offset)
            payload = self._json(url, token)
            if not isinstance(payload, dict) or not isinstance(payload.get('content'), list):
                raise RuntimeError('SmartRecruiters board {} returned an invalid response'.format(token))
            content = payload['content']
            found.extend(content)
            total = payload.get('totalFound')
            if not isinstance(total, int):
                total = len(found)
            if not content:
                break
            offset += len(content)
            if offset > _PAGE_LIMIT:
                raise RuntimeError('SmartRecruiters board {} exceeded the posting page limit'.format(token))
        return found

    @staticmethod
    def _detail_url(token, job_id):
        return BASE_URL.format(urllib.parse.quote(token, safe='')) + '/' + urllib.parse.quote(str(job_id), safe='')

    def _json(self, url, token):
        request = urllib.request.Request(url, headers={
            'Accept': 'application/json',
            'User-Agent': 'ai-job-search-system/1.0',
        })
        try:
            response_context = (self.opener(request, timeout=self.timeout)
                                if self.opener is urllib.request.urlopen else self.opener(request))
            with response_context as response:
                body = response.read().decode('utf-8')
        except urllib.error.HTTPError as error:
            raise RuntimeError('SmartRecruiters board {} returned HTTP {}'.format(token, error.code)) from error
        except urllib.error.URLError as error:
            raise RuntimeError('SmartRecruiters board {} unavailable: {}'.format(token, error.reason)) from error
        try:
            return json.loads(body)
        except ValueError as error:
            raise RuntimeError('SmartRecruiters board {} returned an invalid response'.format(token)) from error

    @staticmethod
    def _normalize(company, job):
        sections = (job.get('jobAd') or {}).get('sections') or {}
        parts = []
        for key in ('jobDescription', 'qualifications', 'additionalInformation'):
            section = sections.get(key) or {}
            if isinstance(section, dict) and section.get('text'):
                parts.append(section['text'])
        location = job.get('location') or {}
        country = (location.get('country') or '').strip().upper()
        if country == 'ISR':
            country = 'IL'
        if len(country) != 2 or not country.isalpha():
            country = ''
        place = location.get('fullLocation') or ', '.join(
            item for item in (location.get('city'), location.get('region')) if item)
        department = (job.get('department') or {}).get('label') or (job.get('function') or {}).get('label')
        return {
            'company': company,
            'role': job.get('name') or '',
            'posting_url': job.get('postingUrl') or '',
            'application_url': job.get('applyUrl') or '',
            'location': place or '',
            'countries': [country] if country else [],
            'description': _plain_text('\n'.join(parts)),
            'departments': [department] if department else [],
            'source': 'smartrecruiters',
            'source_id': str(job.get('id') or ''),
            'posted_at': job.get('releasedDate'),
            'publication_date_kind': 'first_published',
            'employment_type': (job.get('typeOfEmployment') or {}).get('label') or '',
        }
