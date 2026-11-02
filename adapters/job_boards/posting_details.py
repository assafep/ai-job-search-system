"""Read source posting details before a stateless application agent runs."""

import hashlib
import json
import re
from time import monotonic


def posting_content_bytes(details):
    """Serialize the normalized posting so later stages need no network."""
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
        from adapters.google.journal import normalize_posting_url
        suffix = ' - ' + hashlib.sha256(normalize_posting_url(posting_url).encode()).hexdigest()[:16]
    return 'Job Posting - {}{}.json'.format(label[:180], suffix)


def fetch_posting_details(posting_url, *, source_reader=None, deadline=None, description_reader=None):
    """Compatibility facade; all network retrieval goes through the framework."""
    from .framework import AdapterFramework
    from .interface import BookmarkContext
    framework = AdapterFramework(source_reader=source_reader, deadline=deadline,
                                 description_reader=description_reader, clock=monotonic)
    return framework.fetch_details(BookmarkContext(posting_url)).unwrap()
