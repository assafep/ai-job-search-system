"""Public oracle posting adapter."""

import json
import re
import urllib.parse

from .interface import JobBoardAdapter, UnsupportedOperation
from .parsing import text as _text


def _oracle_endpoint(posting_url):
    parsed = urllib.parse.urlsplit(posting_url)
    if not (parsed.hostname or '').endswith('.oraclecloud.com'):
        return None
    match = re.search(r'/job/(\d+)(?:/|$)', parsed.path)
    if not match:
        return None
    endpoint = '{}://{}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails'
    endpoint = endpoint.format(parsed.scheme or 'https', parsed.hostname)
    return endpoint + '?onlyData=true&finder=ById;Id={}&expand=all'.format(match.group(1))



def _akamai_oracle_endpoint(posting_url):
    """Map Akamai's branded Oracle job alias to its exact public ATS record."""
    parsed = urllib.parse.urlsplit(posting_url)
    if (parsed.hostname or '').lower() != 'jobs.akamai.com':
        return None
    match = re.fullmatch(r'/en/sites/CX_1/job/(\d+)/?', parsed.path)
    if not match:
        return None
    return ('https://fa-extu-saasfaprod1.fa.ocs.oraclecloud.com/'
            'hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails'
            '?onlyData=true&finder=ById;Id={}&expand=all').format(match.group(1))



def _extract_oracle(source):
    payload = json.loads(source)
    items = payload.get('items') or []
    if not items:
        return ''
    job = items[0]
    parts = [job.get('Title') or '']
    for label, key in (
            ('Description', 'ExternalDescriptionStr'),
            ('Responsibilities', 'ExternalResponsibilitiesStr'),
            ('Requirements', 'ExternalQualificationsStr')):
        if job.get(key):
            parts.append(label + '\n' + _text(job[key]))
    for label, key in (('Location', 'PrimaryLocation'), ('Work arrangement', 'WorkplaceType')):
        if job.get(key):
            parts.append(label + '\n' + _text(str(job[key])))
    return '\n\n'.join(part for part in parts if part.strip())



class OracleJobBoard(JobBoardAdapter):
    provider_name = 'oracle'
    hosts = ('jobs.akamai.com',)
    host_suffixes = ('oraclecloud.com',)

    def fetch_details_api(self, context, transport):
        url = _oracle_endpoint(context.posting_url) or _akamai_oracle_endpoint(context.posting_url)
        if not url:
            raise UnsupportedOperation('Not an exact Oracle posting URL')
        payload = transport.read_json(url)
        job_id = re.search(r'/job/(\d+)', urllib.parse.urlsplit(context.posting_url).path)[1]
        items = payload.get('items') or []
        if len(items) != 1 or (items[0].get('Id') is not None and str(items[0]['Id']) != job_id):
            raise ValueError('Oracle API returned a different posting')
        return transport.details(context.posting_url, url, _extract_oracle(json.dumps(payload)))
