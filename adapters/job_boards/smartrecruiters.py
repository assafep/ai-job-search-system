"""Read-only SmartRecruiters public postings adapter.

releasedDate is when the company released the posting, so it is a publication
time. The list call has no job text. Descriptions are read only for postings
whose list location is Israel.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .interface import EmployerJobBoard, UnsupportedOperation

from .locations import ISRAEL_LOCATION

from .parsing import plain_text as _plain_text


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


class SmartRecruitersJobBoard(EmployerJobBoard):
    provider_name = 'smartrecruiters'
    hosts = ('jobs.smartrecruiters.com',)

    def discovery_pages(self, context):
        for token, company in (context.boards or self.boards).items():
            yield 'https://careers.smartrecruiters.com/' + urllib.parse.quote(token, safe=''), company

    def fetch_details_api(self, context, transport):
        from .parsing import text
        match = re.match(r'^/([^/]+)/(\d+)(?:-|/|$)', urllib.parse.urlsplit(context.posting_url).path)
        if not match:
            raise UnsupportedOperation('Not an exact SmartRecruiters posting URL')
        url = self._detail_url(*match.groups())
        payload = transport.read_json(url)
        if payload.get('id') is not None and str(payload['id']) != match[2]:
            raise ValueError('SmartRecruiters API returned a different posting')
        sections = (payload.get('jobAd') or {}).get('sections') or {}
        description = text('\n'.join((sections.get(key) or {}).get('text') or ''
                           for key in ('jobDescription', 'qualifications', 'additionalInformation')))
        return transport.details(context.posting_url, url, description)

    def __init__(self, boards=None, opener=None, timeout=30):
        if boards is not None and (not isinstance(boards, dict) or not boards):
            raise ValueError('at least one SmartRecruiters company identifier is required')
        self.boards = boards or {}
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def list_jobs(self, *, boards=None, _transport=None):
        postings = []
        for token, company in (boards if boards is not None else self.boards).items():
            listed = [job for job in self._listed(token, _transport) if _in_israel(job)]
            failures = 0
            for job in listed:
                job_id = job.get('id')
                if not job_id:
                    failures += 1
                    continue
                try:
                    detail = self._json(self._detail_url(token, job_id), token, _transport)
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

    def _listed(self, token, _transport=None):
        quoted = urllib.parse.quote(token, safe='')
        offset = 0
        found = []
        total = None
        while total is None or offset < total:
            url = BASE_URL.format(quoted) + '?limit=100&offset={}'.format(offset)
            payload = self._json(url, token, _transport)
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

    def _json(self, url, token, _transport=None):
        request = urllib.request.Request(url, headers={
            'Accept': 'application/json',
            'User-Agent': 'ai-job-search-system/1.0',
        })
        try:
            response_context = self._open(request, _transport)
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
