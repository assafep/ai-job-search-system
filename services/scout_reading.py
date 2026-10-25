"""Turn a scout model's reading of one posting URL into saved posting content.

This is the single entry point for extracting structured posting data from
agent output. It handles the read-posting agent (flat format).
"""

from application_service.journal_format import normalize_posting_url


MIN_DESCRIPTION = 500


class PostingReadFailure(ValueError):
    """The model reported an ordinary failure to read the posting page."""

    def __init__(self, message, attempt=None):
        super().__init__(message)
        self.attempt = attempt or {}


def bookmark_attempt_lines(attempt):
    """Same five lines as every other workflow outcome."""
    from .event_attempt import attempt_lines
    return attempt_lines(attempt)


def posting_from_model(posting_url, company, role, output):
    """Return the extracted posting, None when it is not a fit, or raise when unread.

    Handles the read-posting agent output format:
    {"company", "role", "posting_url", "description"}
    """
    if not isinstance(output, dict):
        raise ValueError('agent must return an object')
    failure = str(output.get('read_failure') or '').strip()
    if failure:
        raise PostingReadFailure(failure)

    return _from_flat(posting_url, company, role, output)


def _from_flat(posting_url, company, role, output):
    """Extract posting from read-posting agent's flat output."""
    candidate_url = output.get('posting_url') or ''
    if normalize_posting_url(candidate_url) != normalize_posting_url(posting_url):
        raise ValueError('posting URL mismatch: expected {}, got {}'.format(posting_url, candidate_url))
    description = str(output.get('description') or '').strip()
    if len(description) < MIN_DESCRIPTION:
        raise ValueError('extracted posting is too short to evaluate')
    return {
        'posting_url': posting_url,
        # Preserve the exact retrieval source when the agent had to use a
        # verified canonical page or public job-data endpoint. The supplied
        # URL remains the identity of the application.
        'source_url': output.get('resolved_url') or posting_url,
        'description': description,
        'company': company,
        'role': role,
    }
