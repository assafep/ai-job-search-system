"""Clear Rejected, Invalidated, and Duplicate applications from the main journal.

Rejected and Invalidated applications go through the completion API, which
records the completed document and then removes the tab. Duplicate
applications are removed from the journal and do not call that API.
"""

TERMINAL_STATES = ('Rejected', 'Invalidated', 'Duplicate')


def application_status(application):
    return application.status if hasattr(application, 'status') else application.get('status')


def application_id(application):
    return application.application_id if hasattr(application, 'application_id') else application.get('application_id')


def run_completion_stage(applications, complete, remove):
    results = []
    for application in applications:
        status = application_status(application)
        if status not in TERMINAL_STATES:
            continue
        identity = application_id(application)
        if status == 'Rejected':
            complete(identity, 'rejected')
            results.append({'application_id': identity, 'status': status, 'action': 'completed'})
        elif status == 'Invalidated':
            complete(identity, 'invalidated')
            results.append({'application_id': identity, 'status': status, 'action': 'completed'})
        else:
            remove(identity)
            results.append({'application_id': identity, 'status': status, 'action': 'removed'})
    return results


def execute(persistence=None):
    """The Application Service owns journal reads, completion, and tab removal."""
    if persistence is None:
        from .application_service_client import ApplicationServiceClient
        persistence = ApplicationServiceClient()
    from .bulk_updates import item, require_success
    applications = persistence.list_terminal_journal_items()
    updates, selected = [], []
    for application in applications:
        status = application_status(application)
        if status not in TERMINAL_STATES:
            continue
        operation = 'remove_duplicate' if status == 'Duplicate' else 'complete_application'
        data = None if status == 'Duplicate' else {'reason': status.lower()}
        updates.append(item(operation, application=application, data=data))
        selected.append(application)
    response = persistence.bulk_update_items(updates, batch_size=5)
    require_success(response)
    return [{'application_id': application_id(a), 'status': application_status(a),
             'action': 'removed' if application_status(a) == 'Duplicate' else 'completed'}
            for a in selected]
