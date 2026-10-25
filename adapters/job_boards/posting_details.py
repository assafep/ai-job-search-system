"""Read source posting details before a stateless application agent runs."""

import base64
import html
import hashlib
import json
import os
import re
from time import monotonic
import urllib.error
import urllib.parse
import urllib.request


def posting_content_bytes(details):
    """Serialize the normalized posting so later workflows need no network."""
    saved = {
        'posting_url': details['posting_url'],
        'source_url': details.get('source_url') or details['posting_url'],
        'description': details['description'],
    }
    # Our id is not on the employer page. Record it when the bookmark has one.
    if details.get('application_id'):
        saved['application_id'] = details['application_id']
    return json.dumps(saved, ensure_ascii=False, indent=2).encode('utf-8')


def parse_saved_posting(content):
    if isinstance(content, bytes):
        content = content.decode('utf-8')
    data = json.loads(content)
    if not isinstance(data, dict) or not data.get('posting_url') or len(str(data.get('description') or '')) < 500:
        raise ValueError('saved posting content is incomplete')
    return data


def posting_artifact_name(company, role, posting_url=None):
    label = '{} - {}'.format(company, role)
    label = re.sub(r'[\\/:*?"<>|]+', '-', label).strip()
    suffix = ''
    if posting_url:
        from application_service.journal_format import normalize_posting_url
        suffix = ' - ' + hashlib.sha256(normalize_posting_url(posting_url).encode()).hexdigest()[:16]
    return 'Job Posting - {}{}.json'.format(label[:180], suffix)


def find_posting_artifact(artifacts, events, posting_url=None):
    """Find the saved posting for this application.

    A journal "Posting content" bullet is used when it names a file. That
    bullet is not on every bookmark, so the filename hash of the posting URL
    is the fallback, then the only saved posting in the folder.
    """
    from dataclasses import asdict, is_dataclass
    def plain(value):
        return asdict(value) if is_dataclass(value) else value

    postings = [plain(item) for item in artifacts if str(plain(item).get('name', '')).startswith('Job Posting - ')]
    if not postings:
        raise ValueError('saved posting content is missing')
    for event in reversed(events or []):
        for bullet in (plain(event).get('data') or {}).get('bullets', []):
            if not str(bullet).startswith('Posting content:'):
                continue
            selected = next((item for item in postings
                             if (item.get('drive_file_id') or item.get('artifact_id') or '') in str(bullet)), None)
            if selected:
                return selected
    if posting_url:
        from application_service.journal_format import normalize_posting_url
        suffix = hashlib.sha256(normalize_posting_url(posting_url).encode()).hexdigest()[:16]
        named = [item for item in postings if suffix in str(item.get('name') or '')]
        if len(named) == 1:
            return named[0]
    if len(postings) == 1:
        return postings[0]
    raise ValueError('multiple saved postings without an authoritative content link')


def read_posting_artifact(persistence, application_id, expected_posting_url):
    """Read and validate a saved posting artifact from the Application Service.

    This is the single way to read a previously saved posting. It finds the
    artifact, reads it, parses it, and validates the URL matches.
    """
    from dataclasses import asdict, is_dataclass
    from application_service.journal_format import normalize_posting_url

    artifacts = [asdict(item) if is_dataclass(item) else item
                 for item in persistence.list_artifacts(application_id)]
    events = persistence.list_events(application_id)
    selected = find_posting_artifact(artifacts, events, expected_posting_url)
    raw = persistence.read_artifact(application_id, selected['artifact_id'])
    content = raw.get('content')
    if content is None:
        content = base64.b64decode(raw['content_base64'], validate=True)
    posting = parse_saved_posting(content)
    if expected_posting_url and normalize_posting_url(posting['posting_url']) != normalize_posting_url(expected_posting_url):
        raise ValueError('saved posting content belongs to another position')
    return posting


def _text(value):
    value = html.unescape(value)
    value = re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>', '', value, flags=re.S | re.I)
    value = re.sub(r'<[^>]+>', '\n', value)
    return re.sub(r'\n\s*\n+', '\n', html.unescape(value)).strip()


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


def fetch_posting_details(posting_url):
    # Public posting APIs identify the exact job without rendering the careers
    # site: https://docs.greenhouse.io/job-board.html and
    # https://developers.smartrecruiters.com/docs/endpoints .
    parsed = urllib.parse.urlsplit(posting_url)
    api_url = None
    api_headers = {}
    hibob = _hibob_endpoint(posting_url)
    hibob_job_id = None
    if hibob:
        api_url, hibob_job_id, tenant = hibob
        api_headers['companyIdentifier'] = tenant
    oracle_url = _oracle_endpoint(posting_url)
    if oracle_url:
        api_url = oracle_url
    akamai_oracle_url = _akamai_oracle_endpoint(posting_url)
    if akamai_oracle_url:
        api_url = akamai_oracle_url
    if parsed.hostname in {'boards.greenhouse.io', 'job-boards.greenhouse.io'}:
        match = re.fullmatch(r'/([^/]+)/jobs/(\d+)/?', parsed.path)
        if match:
            api_url = 'https://boards-api.greenhouse.io/v1/boards/{}/jobs/{}'.format(*match.groups())
    elif parsed.hostname == 'jobs.smartrecruiters.com':
        match = re.match(r'^/([^/]+)/(\d+)(?:-|/|$)', parsed.path)
        if match:
            api_url = 'https://api.smartrecruiters.com/v1/companies/{}/postings/{}'.format(*match.groups())
    urls = ([api_url] if api_url else []) + [posting_url]
    timeout = float(os.environ.get('POSTING_REQUEST_TIMEOUT_SECONDS', '12'))
    budget = float(os.environ.get('POSTING_FETCH_BUDGET_SECONDS', '25'))
    if timeout <= 0 or budget <= 0:
        raise ValueError('posting fetch timeouts must be positive')
    deadline = monotonic() + budget
    failures = []
    linkedin = re.search(r'linkedin\.com/jobs/view/[^/?]*?(\d+)(?:[/?]|$)', posting_url)
    if linkedin:
        # The mirror uses the exact LinkedIn job ID, avoiding title-only matches.
        urls.append('https://jobify360.co.il/jobs/{}-in'.format(linkedin.group(1)))
    for url in urls:
        remaining = deadline - monotonic()
        if remaining <= 0:
            failures.append('retrieval time budget exhausted')
            break
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0',
                'Cache-Control': 'no-cache',
                'Pragma': 'no-cache',
            }
            if url == api_url:
                headers.update(api_headers)
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=min(timeout, remaining)) as response:
                source = response.read(3000000).decode('utf-8', 'replace')
            descriptions = []
            if url == api_url:
                if oracle_url or akamai_oracle_url:
                    descriptions.append(_extract_oracle(source))
                elif hibob_job_id:
                    descriptions.append(_extract_hibob(source, api_url, hibob_job_id))
                elif urllib.parse.urlsplit(url).hostname == 'boards-api.greenhouse.io':
                    payload = json.loads(source)
                    descriptions.append(_text(payload.get('content') or ''))
                else:
                    payload = json.loads(source)
                    sections = (payload.get('jobAd') or {}).get('sections') or {}
                    descriptions.append(_text('\n'.join((sections.get(key) or {}).get('text') or ''
                        for key in ('jobDescription', 'qualifications', 'additionalInformation'))))
            if urllib.parse.urlsplit(url).hostname == 'www.comeet.com':
                descriptions.append(_extract_comeet(source))
            # Nimble's Webflow page is a shell. It publishes the exact
            # Comeet API URL and loads the posting sections client-side.
            if urllib.parse.urlsplit(url).hostname == 'www.nimbleway.com':
                match = re.search(r'const position_path = `([^`]+)`', source)
                if match:
                    nimble_url = match.group(1).replace(
                        '${positionId}', urllib.parse.parse_qs(parsed.query).get('uid', [''])[0])
                    if nimble_url not in urls:
                        urls.append(nimble_url)
            if 'comeet.co/careers-api/2.0/company/' in url:
                payload = json.loads(source)
                details = payload.get('details') or []
                descriptions.append('\n\n'.join(
                    '{}\n{}'.format(item.get('name'), _text(str(item.get('value'))))
                    for item in details
                    if isinstance(item, dict) and item.get('name') and item.get('value')))
            for block in re.findall(r'<script[^>]*type\s*=\s*[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', source, re.S | re.I):
                try:
                    data = json.loads(block)
                except ValueError:
                    continue
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    if isinstance(entry, dict):
                        graph = entry.get('@graph', [])
                        if isinstance(graph, list):
                            entries.extend(graph)
                        types = entry.get('@type', [])
                        types = [types] if isinstance(types, str) else types
                        if isinstance(types, list) and 'JobPosting' in types and isinstance(entry.get('description'), str):
                            descriptions.append(_text(entry['description']))
            markup = re.search(r'<div[^>]*class=[\"\'][^\"\']*show-more-less-html__markup[^\"\']*[\"\'][^>]*>(.*?)</div>', source, re.S)
            if markup:
                descriptions.append(_text(markup.group(1)))
            if not descriptions and urllib.parse.urlsplit(url).hostname == 'jobify360.co.il':
                body = _text(source)
                start = body.find('Description')
                end = body.find('שאלות ותשובות', max(0, start))
                if start >= 0:
                    descriptions.append(body[start:end if end > start else None])
            description = max(descriptions, key=len) if descriptions else ''
            if len(description) >= 500:
                return {'posting_url': posting_url, 'source_url': url, 'description': description}
            failures.append('no full job description in response')
        except urllib.error.HTTPError as exc:
            failures.append('HTTP ' + str(exc.code))
        except (OSError, ValueError) as exc:
            failures.append(type(exc).__name__)
    raise ValueError('full job posting unavailable; package preparation must stop: ' + posting_url +
                     ' (' + '; '.join(failures) + ')')
