"""Shared validation for one-shot application submission outcomes."""

from urllib.parse import urlsplit


OUTCOMES = frozenset({
    'Submitted successfully', 'Already submitted', 'Prepared but not submitted',
    'Submission unconfirmed', 'Blocked',
})
SUCCESS_OUTCOMES = frozenset({'Submitted successfully', 'Already submitted'})


def _nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def _web_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (parsed.scheme in {'http', 'https'} and bool(parsed.hostname)
            and not parsed.username and not parsed.password)


def validate_submission_outcome(outcome, *, native=False, begun=False):
    """Validate a structured result without changing it or causing side effects."""
    if (not isinstance(outcome, dict) or not isinstance(outcome.get('status'), str)
            or outcome['status'] not in OUTCOMES):
        raise ValueError('submission returned an invalid outcome; attempt remains unconfirmed')

    status = outcome['status']
    if 'requires_human_verification' in outcome and not isinstance(outcome['requires_human_verification'], bool):
        raise ValueError('requires_human_verification must be a boolean')
    if status in SUCCESS_OUTCOMES:
        if outcome.get('requires_human_verification'):
            raise ValueError('successful submission cannot also require human verification')
        if not any(_nonempty_text(outcome.get(key)) for key in ('confirmation_text', 'application_reference')):
            raise ValueError('successful submission supplied no confirmation evidence; attempt remains unconfirmed')
        if native and (not begun or not _nonempty_text(outcome.get('confirmation_text'))
                       or not _web_url(outcome.get('observed_url'))):
            raise ValueError('native success requires a begun attempt, observed confirmation text and page URL')
    else:
        if native and status == 'Submission unconfirmed' and not begun:
            raise ValueError('native unconfirmed outcome requires a begun attempt')
        blockers = outcome.get('blockers')
        if (not isinstance(blockers, list) or not blockers
                or any(not _nonempty_text(blocker) for blocker in blockers)):
            raise ValueError('failed or unconfirmed submission requires a non-empty explanation')
    return outcome