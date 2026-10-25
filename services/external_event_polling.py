"""Runtime-owned event polling; persist checkpoints only through the data API."""

import hashlib
import json
import re
from datetime import datetime, timezone

from adapters.contracts import calendar_event, incoming_email

from adapters.google_events import GoogleEventError, GoogleEventSource, SCOPES
from application_service.storage_layout import workload_state_path
from .event_dispatcher import dispatch_normalized
from .runtime_config import load


def matching_application(text, applications):
    matches = []
    for app in applications:
        company = app.get('company', '').strip()
        if len(company) < 3 or not re.search(r'(?<!\w)' + re.escape(company) + r'(?!\w)', text, re.I):
            continue
        matches.append(app)
    if len(matches) > 1:
        role_matches = [a for a in matches if a.get('role') and a['role'].lower() in text.lower()]
        matches = role_matches
    return matches[0] if len(matches) == 1 else None


# Prepared and Manual Apply Required still need an employer receipt. Applied and
# Interviewing still need a rejection or a later confirmation.
MAIL_STATUSES = {'Prepared', 'Manual Apply Required', 'Applied', 'Interviewing'}
MAIL_SIGNAL = re.compile(
    r'interview|application|applying|applied|candidate|recruit|hiring|'
    r'confirmation|we received|thank you for|unfortunately|regret to|'
    r'not moving forward|not selected|unsuccessful|'
    r'ראיון|מועמד|גיוס|דחי|מועמדות',
    re.I)


def poll(persistence, registry, source=None, complete=None, workflows=None):
    source = source or GoogleEventSource()
    applications = [a for a in persistence.list_applications() if a['status'] in MAIL_STATUSES]
    reports = {}
    for kind, workflow in [('gmail', 'handle-incoming-email'), ('calendar', 'prepare-interview')]:
        if workflows is not None and workflow not in workflows:
            continue
        report = {'source': kind, 'workflow': workflow, 'observed_at': datetime.now(timezone.utc).isoformat(),
                  'scanned': 0, 'queued': 0, 'already_known': 0, 'unmatched_or_ambiguous': 0}
        try:
            values, truncated = source.gmail([a['company'] for a in applications]) if kind == 'gmail' else source.calendar()
            report.update(status='ok', truncated=truncated, scanned=len(values))
            for raw in values:
                if kind == 'gmail' and not MAIL_SIGNAL.search(raw.get('subject', '') + ' ' + raw.get('body', '')):
                    report['unmatched_or_ambiguous'] += 1
                    continue
                if kind == 'calendar' and not re.search(r'interview|ראיון|recruit|hiring', raw['title'] + ' ' + raw.get('description', ''), re.I):
                    report['unmatched_or_ambiguous'] += 1
                    continue
                material = '\n'.join(raw.get(key, '') for key in ('sender', 'subject', 'title', 'body', 'description'))
                app = matching_application(material, applications)
                if not app:
                    report['unmatched_or_ambiguous'] += 1
                    continue
                raw['application_id'] = app['application_id']
                event = incoming_email(raw) if kind == 'gmail' else calendar_event(raw)
                if kind == 'calendar':
                    event['payload']['description'] = raw.get('description', '')[:30000]
                before = set(registry.records)
                envelope = dispatch_normalized(event, registry)
                report['queued' if envelope['workload_id'] not in before else 'already_known'] += 1
            if kind == 'gmail' and complete is not None:
                from .completion_scan import scan_completions
                report['completed'] = len(scan_completions(values, applications, complete))
        except GoogleEventError as error:
            report.update(status='blocked', http_status=error.status, reason=error.reason, required_scope=SCOPES[kind])
        reports[workflow] = report
        content = json.dumps(report).encode()
        persistence.store_file(workload_state_path(workflow, 'event-polling/latest.json'), content, 'application/json',
                               'event-poll-' + hashlib.sha256(content).hexdigest())
    return reports


def main():
    from .application_service_client import ApplicationServiceClient
    from .workload_registry import registry_from_environment
    load(require=True)
    print(json.dumps(poll(ApplicationServiceClient(), registry_from_environment()), indent=2))


if __name__ == '__main__':
    main()
