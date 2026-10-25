"""Continuously repair dead-lettered postings whose source URL was unreadable.

Only URL-reading failures are eligible. Other dead-lettered system errors are
left for the normal operator recovery process.
"""

import argparse
import re
import sys
from dataclasses import asdict, is_dataclass
from time import sleep

from adapters.job_boards.posting_details import (
    fetch_posting_details, posting_artifact_name, posting_content_bytes)
from application_service.journal_format import ALL_STATES, parse_transition_arrow


DEAD_LETTERS = 'Dead Letters Queue'
READ_FAILURE_MARKERS = (
    'could not read', 'cannot read',
    'unable to read', 'inaccessible', 'unreadable',
    'full job posting unavailable', 'extracted posting is too short',
)
MOVE_RE = re.compile(r'Moved from (.+?) to (.+)$')


def plain(value):
    return asdict(value) if is_dataclass(value) else value


def event_bullets(event):
    data = plain(event) or {}
    return [str(item) for item in (data.get('data') or {}).get('bullets') or []]


def is_url_read_failure(events):
    """Return whether journal evidence identifies a source URL read failure."""
    bullets = [bullet.strip() for event in events for bullet in event_bullets(event)]
    text = '\n'.join(bullets).lower()
    if any(marker in text for marker in READ_FAILURE_MARKERS):
        return True
    for bullet in bullets:
        prefix, separator, detail = bullet.partition(':')
        if separator and prefix.strip().lower() == 'read failure' and detail.strip():
            return True
    return False


def previous_state(events):
    """Recover the state immediately before the transition into the DLQ."""
    for event in reversed(events):
        data = plain(event) or {}
        payload = data.get('data') or {}
        if payload.get('to') == DEAD_LETTERS and payload.get('from') in ALL_STATES:
            return payload['from']
        for bullet in event_bullets(event):
            parsed = parse_transition_arrow(bullet)
            if parsed and parsed[1] == DEAD_LETTERS and parsed[0] in ALL_STATES:
                return parsed[0]
        for bullet in reversed(bullets):
            match = MOVE_RE.match(bullet)
            if match and match.group(2) == DEAD_LETTERS and match.group(1) in ALL_STATES:
                return match.group(1)
    return None


def repair_application(persistence, application):
    """Repair one eligible application, returning a structured result."""
    app = plain(application)
    app_id = app['application_id']
    events = persistence.list_events(app_id)
    if not is_url_read_failure(events):
        return {'application_id': app_id, 'status': 'skipped', 'reason': 'not a URL read failure'}
    target = previous_state(events)
    if target not in ALL_STATES or target == DEAD_LETTERS:
        return {'application_id': app_id, 'status': 'skipped', 'reason': 'pre-DLQ state is not recoverable'}

    posting = fetch_posting_details(app['posting_url'])
    if len(str(posting.get('description') or '').strip()) < 500:
        raise ValueError('verified URL response contains an incomplete posting')

    from .event_attempt import with_attempt
    artifact_key = 'dlq-url-repair-artifact-' + app_id
    artifact = plain(persistence.store_artifact(
        app_id,
        posting_artifact_name(app.get('company', ''), app.get('role', ''), app['posting_url']),
        posting_content_bytes({
            'posting_url': app['posting_url'],
            'source_url': posting.get('source_url') or app['posting_url'],
            'description': posting['description'],
        }),
        'application/json',
        artifact_key,
    ))
    if artifact:
        reference = ('https://drive.google.com/file/d/{}/view'.format(artifact['drive_file_id'])
                     if artifact.get('drive_file_id') else artifact['artifact_id'])
        persistence.append_event(app_id, 'posting_content_repaired', {
            'source': 'DLQ URL repair monitor', 'bullets': with_attempt(['Posting content: ' + reference]),
        }, artifact_key + '-reference')
    transition_key = 'dlq-url-repair-transition-' + app_id
    persistence.transition(app_id, target, {
        'source': 'DLQ URL repair monitor',
        'bullets': with_attempt([
            'Verified posting URL after dead-lettered read failure.',
            'Posting content saved by the URL adapter.',
        ]),
    }, transition_key)
    return {'application_id': app_id, 'status': 'repaired', 'restored_state': target}


def repair_once(persistence, reporter=None):
    """Inspect and repair the current DLQ once."""
    results = []
    for application in persistence.list_applications(DEAD_LETTERS) or []:
        try:
            result = repair_application(persistence, application)
        except Exception as error:
            result = {'application_id': plain(application)['application_id'],
                      'status': 'waiting', 'error': str(error)}
        results.append(result)
        if reporter:
            reporter(result)
    return results


def watch(persistence, interval=30, reporter=None, iterations=None, sleeper=sleep):
    """Poll until interrupted; transient API/fetch failures do not stop it."""
    if interval <= 0:
        raise ValueError('interval must be positive')
    count = 0
    while iterations is None or count < iterations:
        try:
            results = repair_once(persistence, reporter=reporter)
            if reporter:
                reporter({'status': 'poll_complete', 'processed': len(results)})
        except Exception as error:
            if reporter:
                reporter({'status': 'monitor_error', 'error': str(error)})
        count += 1
        if iterations is not None and count >= iterations:
            break
        sleeper(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval', type=float, default=30,
                        help='Seconds between DLQ polls')
    args = parser.parse_args(argv)
    from .application_service_client import ApplicationServiceClient

    def report(result):
        print(result, flush=True)

    try:
        watch(ApplicationServiceClient(), interval=args.interval, reporter=report)
    except KeyboardInterrupt:
        sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
