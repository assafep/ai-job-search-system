"""Public hibob posting adapter."""

import json
import re
import urllib.parse

from .interface import JobBoardAdapter, UnsupportedOperation
from .parsing import text as _text


def _hibob_endpoint(posting_url):
    """Return the public HiBob job-ad endpoint and exact job id, if present."""
    parsed = urllib.parse.urlsplit(posting_url)
    host = (parsed.hostname or '').lower()
    match = re.fullmatch(r'/jobs/([0-9a-f-]+)/?', parsed.path, re.I)
    if match and host.endswith('.careers.hibob.com'):
        tenant = host.split('.', 1)[0]
        endpoint = '{}://{}/api/job-ad'.format(parsed.scheme or 'https', host)
        return endpoint, match.group(1), tenant
    return None



def _extract_hibob(source, endpoint, job_id):
    payload = json.loads(source)
    for job in payload.get('jobAdDetails') or []:
        if str(job.get('id') or '').lower() != job_id.lower():
            continue
        parts = []
        for label in ('title', 'description', 'responsibilities', 'requirements', 'benefits'):
            value = job.get(label)
            if value:
                parts.append(label.replace('_', ' ').title() + '\n' + _text(str(value)))
        if job.get('site') or job.get('country') or job.get('workspaceType'):
            parts.append('Location and work arrangement\n' + ', '.join(
                str(value) for value in (job.get('site'), job.get('country'), job.get('workspaceType')) if value))
        return '\n\n'.join(part for part in parts if part.strip())
    return ''



class HiBobJobBoard(JobBoardAdapter):
    provider_name = 'hibob'
    host_suffixes = ('careers.hibob.com',)

    def fetch_details_api(self, context, transport):
        endpoint = _hibob_endpoint(context.posting_url)
        if not endpoint:
            raise UnsupportedOperation('Not an exact HiBob posting URL')
        url, job_id, tenant = endpoint
        source = transport.read_text(url, {'companyIdentifier': tenant, 'Accept': 'application/json'})
        return transport.details(context.posting_url, url, _extract_hibob(source, url, job_id))
