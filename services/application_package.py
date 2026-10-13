"""Validate and reuse saved packages; readiness has no human-review gate."""

import base64
import re
import unicodedata
from dataclasses import asdict
from io import BytesIO

from pypdf import PdfReader


def normalized_pdf_text(value):
    """PDF extraction may wrap titles or use typographic punctuation."""
    value = unicodedata.normalize('NFKC', value).casefold()
    value = value.translate(str.maketrans({'\u2013': '-', '\u2014': '-', '\u00ad': '', '\u200b': ''}))
    return re.sub(r'\s+', ' ', value).strip()


def cover_matches_application(cover, application):
    cover = normalized_pdf_text(cover)
    return all(normalized_pdf_text(application[key]) in cover for key in ('company', 'role'))


def saved_package(persistence, application):
    artifacts = [a if isinstance(a, dict) else asdict(a) for a in (persistence.list_artifacts(application['application_id']) or [])]
    candidates = [a for a in artifacts
                  if ' Cover Letter & Resume - ' in a.get('name', '') and a['name'].lower().endswith('.pdf')]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError('multiple application packages require reconciliation before readiness')
    artifact = candidates[0]
    raw = persistence.read_artifact(application['application_id'], artifact['artifact_id'])
    content = raw.get('content')
    if content is None:
        content = base64.b64decode(raw['content_base64'], validate=True)
    pdf = PdfReader(BytesIO(content))
    if len(pdf.pages) < 2 or not all((page.extract_text() or '').strip() for page in pdf.pages):
        raise ValueError('saved application package contains missing or blank pages')
    if not cover_matches_application(pdf.pages[0].extract_text() or '', application):
        raise ValueError('saved cover letter does not match the employer and role')
    reference = ('https://drive.google.com/file/d/{}/view'.format(artifact['drive_file_id'])
                 if artifact.get('drive_file_id') else artifact['artifact_id'])
    return dict(artifact, reference=reference)


def promote_saved_package(persistence, application, artifact):
    app_id = application['application_id']
    # Stable per-artifact operation identities survive worker restarts.
    key = 'prepared-package-' + app_id + '-' + (artifact.get('drive_file_id') or artifact['artifact_id'])
    events = persistence.list_events(app_id) or []
    recorded = any(artifact['reference'] in str(event.get('data', {}).get('bullets', []))
                   for event in events if isinstance(event, dict))
    if not recorded:
        persistence.append_event(app_id, 'application_package_prepared', {
            'source': 'application agent', 'bullets': ['Application package created successfully.',
                                                    'Application package: ' + artifact['reference']],
        }, key + '-event')
    return persistence.transition(app_id, 'Ready for Auto-Apply', {
        'source': 'application agent', 'bullets': ['Saved application package validated; no human review is required.'],
    }, key + '-ready')
