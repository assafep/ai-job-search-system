from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import Mock
import pytest
from application_service.hybrid_repository import HybridRepository
from application_service.storage_layout import workload_state_path


class Connection:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    @contextmanager
    def transaction(self): yield


@pytest.fixture
def repository(monkeypatch):
    from application_service import initialization, hybrid_repository
    records = {}
    monkeypatch.setenv('AJSS_WORKLOAD_FILE_BACKEND', 'database')
    monkeypatch.setattr(initialization, 'resource', lambda c, key: deepcopy(records.get(key)))
    monkeypatch.setattr(initialization, 'save_resource', lambda c, key, value: records.update({key: deepcopy(value)}))
    @contextmanager
    def lock(*args): yield
    monkeypatch.setattr(hybrid_repository, 'installation_lock', lock)
    journal = Mock()
    journal.artifacts = None
    journal.client.document_id = "journal-test"
    return HybridRepository(journal, Connection), journal


def test_internal_files_use_explicit_database_and_retry_receipts(repository):
    repo, journal = repository
    path = workload_state_path('system-run', 'report.pdf')
    metadata = repo.store_file(path, b'%PDF-original', 'application/pdf', 'first')
    assert repo.read_file(path)['content'] == b'%PDF-original'
    repo.store_file(path, b'%PDF-new', 'application/pdf', 'second')
    assert repo.store_file(path, b'%PDF-original', 'application/pdf', 'first') == metadata
    assert repo.read_file(path)['content'] == b'%PDF-new'
    with pytest.raises(ValueError, match='already used'):
        repo.store_file(path, b'changed', 'application/pdf', 'first')
    journal.store_file.assert_not_called()
    journal.read_file.assert_not_called()


def test_legacy_files_remain_readable_and_other_paths_use_journal(repository):
    repo, journal = repository
    legacy = workload_state_path('submit', 'submission-receipts/old.json')
    journal.read_file.return_value = {'content': b'legacy'}
    assert repo.read_file(legacy)['content'] == b'legacy'
    journal.read_file.assert_called_once_with(legacy)
    repo.store_file('shared/notes.md', b'notes', 'text/markdown', 'notes-key')
    journal.store_file.assert_called_once_with('shared/notes.md', b'notes', 'text/markdown', 'notes-key')
    with pytest.raises(ValueError):
        repo.store_file('../outside', b'no')


def test_database_backend_is_not_an_implicit_fallback(repository, monkeypatch):
    repo, journal = repository
    monkeypatch.setenv('AJSS_WORKLOAD_FILE_BACKEND', 'drive')
    journal.store_file.side_effect = RuntimeError('Drive unavailable')
    with pytest.raises(RuntimeError, match='Drive unavailable'):
        repo.store_file(workload_state_path('system-run', 'report.pdf'), b'%PDF')


def test_nested_domain_calls_reuse_the_locked_connection(repository):
    repo, journal = repository
    calls = []
    def connect():
        calls.append('connect')
        return Connection()
    repo.connection_factory = connect
    journal.get_profile.return_value = {'name': 'Example'}
    with repo.mutation_scope():
        assert repo.get_profile() == {'name': 'Example'}
        assert repo.get_profile() == {'name': 'Example'}
        assert calls == ['connect']
    repo.get_profile()
    assert calls == ['connect', 'connect']
    with pytest.raises(ValueError):
        with repo.mutation_scope():
            raise ValueError('interrupted')
    assert not hasattr(repo._sessions, 'connection')
