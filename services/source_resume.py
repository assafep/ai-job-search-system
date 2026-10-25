"""Read the original résumé through the Application Service, or a legacy local file."""
import base64
import hashlib
import os


def read_source_resume(persistence, profile):
    source = (profile or {}).get('source_resume') or {}
    path = source.get('path')
    if not path:
        raise ValueError('original onboarding resume PDF is required')
    if os.path.isabs(path):
        with open(path, 'rb') as handle:
            content = handle.read()
    else:
        result = persistence.read_file(path)
        content = result.get('content')
        if content is None:
            content = base64.b64decode(result['content_base64'], validate=True)
    if source.get('sha256') and hashlib.sha256(content).hexdigest() != source['sha256']:
        raise ValueError('Resume changed in Drive; rerun applicant onboarding before preparing applications')
    return content
