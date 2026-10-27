"""Read-only public Ashby employer feeds; only explicitly listed postings."""

import json
import urllib.error
import urllib.parse
import urllib.request

from .interface import EmployerJobBoard, UnsupportedOperation
from .parsing import plain_text as _plain_text


BASE_URL = 'https://api.ashbyhq.com/posting-api/job-board/'


class AshbyJobBoard(EmployerJobBoard):
    provider_name = 'ashby'
    hosts = ('jobs.ashbyhq.com',)

    def fetch_details_api(self, context, transport):
        parts = urllib.parse.urlsplit(context.posting_url).path.strip('/').split('/')
        if len(parts) != 2:
            raise UnsupportedOperation('Not an exact Ashby posting URL')
        token, job_id = parts
        url = BASE_URL + urllib.parse.quote(token, safe='')
        payload = transport.read_json(url)
        for job in payload.get('jobs') or []:
            if str(job.get('id')) == job_id and job.get('isListed') is True:
                return transport.details(context.posting_url, url,
                                         self._normalize('', job)['description'])
        raise ValueError('Ashby feed has no matching public posting')

    def discovery_pages(self, context):
        for token, company in (context.boards or self.boards).items():
            yield 'https://jobs.ashbyhq.com/' + urllib.parse.quote(token, safe=''), company

    def __init__(self, boards=None, opener=None, timeout=30):
        if boards is not None and (not isinstance(boards, dict) or not boards):
            raise ValueError('at least one Ashby board token is required')
        self.boards = boards or {}
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def list_jobs(self, *, boards=None, _transport=None):
        postings = []
        for token, company in (boards if boards is not None else self.boards).items():
            request = urllib.request.Request(
                BASE_URL + urllib.parse.quote(token, safe=''),
                headers={'Accept': 'application/json', 'User-Agent': 'ai-job-search-system/1.0'})
            try:
                response_context = self._open(request, _transport)
                with response_context as response:
                    payload = json.loads(response.read().decode('utf-8'))
            except urllib.error.HTTPError as error:
                raise RuntimeError('Ashby board {} returned HTTP {}'.format(token, error.code)) from error
            except urllib.error.URLError as error:
                raise RuntimeError('Ashby board {} unavailable: {}'.format(token, error.reason)) from error
            if not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list):
                raise RuntimeError('Ashby board {} returned an invalid response'.format(token))
            for job in payload['jobs']:
                if job.get('isListed') is True:
                    postings.append(self._normalize(company, job))
        return postings

    @staticmethod
    def _normalize(company, job):
        locations = [job.get('location') or '']
        primary = (job.get('address') or {}).get('postalAddress') or {}
        addresses = [primary]
        for location in job.get('secondaryLocations') or []:
            locations.append(location.get('location') or '')
            addresses.append(location.get('address') or {})
        codes = {'ISR': 'IL', 'USA': 'US', 'GBR': 'GB'}
        countries = {codes.get(address.get('addressCountry'), address.get('addressCountry'))
                     for address in addresses if address.get('addressCountry')}
        return {
            'company': company,
            'role': job.get('title') or '',
            'posting_url': job.get('jobUrl') or '',
            'application_url': job.get('applyUrl') or '',
            'location': '; '.join(filter(None, locations)),
            'countries': sorted(countries),
            'description': job.get('descriptionPlain') or _plain_text(job.get('descriptionHtml')),
            'departments': [job['department']] if job.get('department') else [],
            'source': 'ashby',
            'source_id': job.get('id') or (job.get('jobUrl') or '').rstrip('/').split('/')[-1],
            'posted_at': job.get('publishedAt'),
            'publication_date_kind': 'last_published',
            'is_remote': job.get('isRemote', False),
            'employment_type': job.get('employmentType'),
        }
