"""Terminal journal cleanup within the persistence boundary."""

from .completion import CompletionService


def terminal_items(repository):
    statuses = ('Rejected', 'Invalidated', 'Duplicate')
    if hasattr(repository, 'list_main_journal_applications'):
        return repository.list_main_journal_applications(statuses)
    return [a for a in repository.list_applications()
            if (a.status if hasattr(a, 'status') else a['status']) in statuses]


def complete_application(repository, identity, reason):
    from .completion import normalize_reason
    reason = normalize_reason(reason)
    if hasattr(repository, 'export_for_completion') and getattr(repository, 'artifacts', None):
        from .completion_drive import DriveCompletion, RecordCompletion
        from .user_drive import completion_store
        return CompletionService(repository, DriveCompletion(completion_store(repository.artifacts)),
                                 RecordCompletion(repository)).complete(identity, reason)
    return repository.complete_application(identity, reason)


def complete_terminal(repository):
    applications = terminal_items(repository)
    complete = lambda identity, reason: complete_application(repository, identity, reason)
    results = []
    for app in applications:
        identity = app.application_id if hasattr(app, 'application_id') else app['application_id']
        status = app.status if hasattr(app, 'status') else app['status']
        if status == 'Duplicate':
            if hasattr(repository, 'remove_tab'):
                repository.remove_tab(identity)
            else:
                repository.delete_application(identity)
            action = 'removed'
        else:
            complete(identity, status.lower())
            action = 'completed'
        results.append({'application_id': identity, 'status': status, 'action': action})
    return results
