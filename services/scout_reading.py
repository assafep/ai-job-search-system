"""Turn a scout model's reading of one posting URL into saved posting content."""

from persistence_api.journal_format import normalize_posting_url


MIN_DESCRIPTION = 500


class PostingReadFailure(ValueError):
    """The model reported an ordinary failure to read the posting page."""


def posting_from_model(posting_url, company, role, output):
    """Return the extracted posting, None when it is not a fit, or raise when unread."""
    if not isinstance(output, dict):
        raise ValueError('job-discovery must return an object')
    failure = str(output.get('read_failure') or '').strip()
    if failure:
        raise PostingReadFailure(failure)
    candidates = output.get('candidates') or []
    if not candidates:
        return None
    if (len(candidates) != 1 or
            normalize_posting_url(candidates[0].get('posting_url') or '') != normalize_posting_url(posting_url)):
        raise ValueError('scout selected a different position')
    description = str(candidates[0].get('description') or '').strip()
    if len(description) < MIN_DESCRIPTION:
        raise ValueError('extracted posting is too short to evaluate')
    return {
        'posting_url': posting_url,
        'source_url': posting_url,
        'description': description,
        'company': company,
        'role': role,
    }
