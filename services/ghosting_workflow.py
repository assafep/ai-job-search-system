"""Complete Applied and Interviewing applications with no activity for 14 days.

Applied silence is ghosted. Interviewing silence is expired. The completion
API records the document and removes the journal tab.
"""
from datetime import datetime, timedelta, timezone

SILENCE = timedelta(days=14)
REASONS = {'Applied': 'ghosted', 'Interviewing': 'expired'}


def _value(item, key):
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _stamp(value):
    text = str(value or '').strip()
    if not text:
        return None
    if len(text) == 10 and text[4] == '-' and text[7] == '-':
        text += 'T00:00:00+00:00'
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def last_activity(application, events):
    stamps = [_stamp(_value(application, 'updated_at')), _stamp(_value(application, 'created_at'))]
    for event in events or []:
        stamps.append(_stamp(_value(event, 'occurred_at')))
        data = _value(event, 'data') or {}
        if isinstance(data, dict):
            stamps.append(_stamp(data.get('date')))
    found = [item for item in stamps if item is not None]
    return max(found) if found else None


def ghosting_reason(application, events, now=None):
    """Return ghosted or expired when the application has been silent for 14 days."""
    status = _value(application, 'status')
    reason = REASONS.get(status)
    if reason is None:
        return None
    moment = now or datetime.now(timezone.utc)
    activity = last_activity(application, events)
    if activity is None or moment - activity < SILENCE:
        return None
    return reason


def execute(client, now=None):
    from .bulk_updates import item, require_success
    results, updates = [], []
    moment = now or datetime.now(timezone.utc)
    for application in client.list_applications() or []:
        status = _value(application, 'status')
        if status not in REASONS:
            continue
        identity = _value(application, 'application_id')
        events = client.list_events(identity) if hasattr(client, 'list_events') else []
        reason = ghosting_reason(application, events, moment)
        if reason is None:
            continue
        updates.append(item('complete_application', application=application,
                            data={'reason': reason, 'inactive_before': last_activity(application, events).isoformat()}))
        results.append({'application_id': identity, 'status': status, 'reason': reason})
    require_success(client.bulk_update_items(updates, batch_size=5))
    return {'workflow': 'identify-ghosting', 'completed': results}
