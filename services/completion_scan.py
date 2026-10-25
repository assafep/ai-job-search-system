"""Find completion emails and finish the matching active application.

This runs on the same schedule as the other external-event polling.
"""
from application_service.completion import completion_reason_from_text


def scan_completions(messages, applications, complete):
    """Call complete(application_id, reason) once per clear completion email."""
    from .external_event_polling import matching_application
    handled = []
    active = [app for app in applications if app.get('status') not in {'Rejected', 'Invalidated', 'Filled', 'Withdrawn'}]
    for message in messages:
        text = (message.get('subject') or '') + '\n' + (message.get('body') or '')
        reason = completion_reason_from_text(text)
        if reason is None:
            continue
        app = matching_application(text, active)
        if app is None:
            continue
        result = complete(app['application_id'], reason)
        handled.append(result)
        active = [item for item in active if item.get('application_id') != app['application_id']]
    return handled
