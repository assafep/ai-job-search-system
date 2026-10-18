import pytest

from persistence_api.google_docs_repository import GoogleDocsRepository
from persistence_api.journal_format import STATE_TABS
from persistence_api.state_machine import validate_transition
from tests.test_google_docs_repository import tab


class MigrationClient:
    def __init__(self, document):
        self.document = document
        self.calls = []
    def get_document(self):
        return self.document
    def batch_update(self, requests, revision_id=None):
        self.calls.append((requests, revision_id))
        for request in requests:
            if 'updateDocumentTabProperties' in request:
                props = request['updateDocumentTabProperties']['tabProperties']
                next(t for t in self.document['tabs'] if t['tabProperties']['tabId'] == props['tabId'])['tabProperties']['title'] = props['title']
            if 'addDocumentTab' in request:
                title = request['addDocumentTab']['tabProperties']['title']
                self.document['tabs'].append(tab('state-' + title, title))


def test_migration_preserves_ready_id_children_and_history_and_is_idempotent():
    child = tab('app', 'Company / Role', parent='ready', paragraphs=[('Original note', True)])
    ready = tab('ready', 'Ready To Apply', children=[child])
    document = {'revisionId': 'rev', 'tabs': [ready] + [tab(name, name) for name in STATE_TABS if name not in {'Ready for Auto-Apply', 'Manual Apply Required'}]}
    repository = GoogleDocsRepository('token', 'doc')
    repository.client = MigrationClient(document)
    assert repository.migrate_application_states()['valid']
    assert any(t['tabProperties']['title'] == 'Dead Letters Queue' for t in document['tabs'])
    assert ready['tabProperties']['tabId'] == 'ready'
    assert ready['childTabs'] == [child]
    assert repository.list_applications(status='Ready for Auto-Apply')[0].application_id == 'app'
    assert repository.migrate_application_states()['valid']
    assert len(repository.client.calls) == 1
    assert repository.client.calls[0][1] == 'rev'


def test_migration_refuses_old_and_new_tabs_coexisting():
    repository = GoogleDocsRepository('token', 'doc')
    repository.client = MigrationClient({'tabs': [tab('old', 'Ready To Apply'), tab('new', 'Ready for Auto-Apply')]})
    with pytest.raises(ValueError, match='ambiguous'):
        repository.migrate_application_states()
    assert repository.client.calls == []


def test_manual_state_is_not_auto_retryable_without_explicit_transition():
    validate_transition('Ready for Auto-Apply', 'Manual Apply Required')
    validate_transition('Manual Apply Required', 'Applied')
    validate_transition('Manual Apply Required', 'Ready for Auto-Apply')
    validate_transition('Bookmarked', 'Manual Apply Required')


def test_migration_refuses_duplicate_dead_letter_tabs():
    repository = GoogleDocsRepository('token', 'doc')
    repository.client = MigrationClient({'tabs': [
        tab('ready', 'Ready for Auto-Apply'),
        tab('dead1', 'Dead Letters Queue'), tab('dead2', 'Dead Letters Queue')]})
    with pytest.raises(ValueError, match='ambiguous'):
        repository.migrate_application_states()
    assert repository.client.calls == []
