"""Read source posting details before a stateless application agent runs."""

import base64
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
        from adapters.google.journal import normalize_posting_url
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
    from adapters.google.journal import normalize_posting_url

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



def fetch_posting_details(posting_url, *, source_reader=None, deadline=None, description_reader=None):
    """Compatibility facade; all network retrieval goes through the framework."""
    from .framework import AdapterFramework
    from .interface import BookmarkContext
    framework = AdapterFramework(source_reader=source_reader, deadline=deadline,
                                 description_reader=description_reader, clock=monotonic)
    return framework.fetch_details(BookmarkContext(posting_url)).unwrap()
