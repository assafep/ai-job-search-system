import json
from concurrent.futures import Future
from pathlib import Path

import pytest

from services.orchestrator import Orchestrator
from services.pipeline_config import validate
from services.workload_registry import WorkloadRegistry


class RecordingPool:
    def __init__(self):
        self.calls = []

    def submit(self, function, *args):
        self.calls.append((function, args))
        return Future()


def test_discovery_runs_without_obsolete_scout_pipeline(tmp_path):
    config = json.loads(Path('config/pipelines.json').read_text())
    config['discovery_workers'] = 4
    path = tmp_path / 'pipelines.json'
    path.write_text(json.dumps(config))
    from application_service.repository import MemoryRepository
    service = Orchestrator(MemoryRepository(), WorkloadRegistry(), None, str(path))
    service.pool.shutdown()
    service.pool = RecordingPool()
    service._maybe_discover()
    assert 'scout' not in service.config['pipelines']
    batches = min(config['discovery_workers'], len(config['discovery_sources']))
    assert len(service.pool.calls) == batches
    assigned = [source for _, (_, batch, budget) in service.pool.calls for source in batch]
    assert sorted(assigned) == sorted(config['discovery_sources'])
    service._maybe_discover()
    assert len(service.pool.calls) == batches
    assert service.write_status()['discovery'] == {'workers': 4, 'active': batches,
                                                  'max_new_postings': 100 - len(service.persistence.journal_tabs())}


@pytest.mark.parametrize('workers', [-1, True, 65, '4'])
def test_discovery_worker_count_is_validated(workers):
    config = json.loads(Path('config/pipelines.json').read_text())
    config['discovery_workers'] = workers
    with pytest.raises(ValueError, match='discovery_workers'):
        validate(config)


def test_total_worker_limit_includes_discovery():
    config = json.loads(Path('config/pipelines.json').read_text())
    config['discovery_workers'] = 64
    config['pipelines']['bookmark']['workers'] = 1
    with pytest.raises(ValueError, match='at most 64'):
        validate(config)


def test_duplicate_identity_check_does_not_download_irrelevant_artifacts(tmp_path):
    from application_service.repository import MemoryRepository
    repository = MemoryRepository()
    handled = repository.create_application('Handled', 'Backend', 'https://example.com/handled')
    repository.transition(handled.application_id, 'Prepared')
    repository.transition(handled.application_id, 'Applied')
    service = Orchestrator(repository, WorkloadRegistry(), None)
    try:
        service._saved_description = lambda app: pytest.fail('identity checks need no posting download')
        assert service._handled_match({'application_id': 'candidate', 'company': 'New',
                                      'role': 'Backend', 'posting_url': 'https://example.com/new'}, None) is None
        assert service._handled_match({'application_id': 'candidate', 'company': 'Handled',
                                      'role': 'Backend', 'posting_url': 'https://example.com/other'}, None)
    finally:
        service.close()


def test_saved_description_cache_refreshes_after_application_changes(monkeypatch):
    calls = []
    monkeypatch.setattr('services.orchestrator.read_posting',
                        lambda persistence, app: calls.append(app['last_event_id']) or {'description': 'saved'})
    service = Orchestrator(None, WorkloadRegistry(), None)
    app = {'application_id': 'one', 'last_event_id': 'first'}
    try:
        assert service._saved_description(app) == 'saved'
        assert service._saved_description(app) == 'saved'
        app['last_event_id'] = 'second'
        assert service._saved_description(app) == 'saved'
        assert calls == ['first', 'second']
    finally:
        service.close()


def test_missing_handled_posting_does_not_fail_the_posting_being_checked(monkeypatch):
    from services.application_service_client import ApplicationServiceError

    def read_posting(persistence, app):
        if app['application_id'] == 'missing':
            raise ApplicationServiceError(
                'Application Service returned 404: {"error": "not found", "field": "\'missing\'"}')
        return {'description': 'saved posting ' * 40}

    monkeypatch.setattr('services.orchestrator.read_posting', read_posting)
    service = Orchestrator(None, WorkloadRegistry(), None)
    try:
        assert service._saved_description({'application_id': 'missing', 'last_event_id': 'e'}) is None

        class Lists:
            def list_applications(self):
                return [
                    {'application_id': 'missing', 'company': 'Gone', 'role': 'Backend',
                     'posting_url': 'https://example.com/gone', 'status': 'Applied', 'last_event_id': 'e'},
                    {'application_id': 'kept', 'company': 'Kept', 'role': 'Backend',
                     'posting_url': 'https://example.com/kept', 'status': 'Applied', 'last_event_id': 'k'},
                ]

        service.persistence = Lists()
        found = service._handled_match(
            {'application_id': 'candidate', 'company': 'Kept', 'role': 'Backend',
             'posting_url': 'https://example.com/kept', 'status': 'Validated', 'last_event_id': 'c'},
            None)
        assert found is not None
        assert found[0]['application_id'] == 'kept'
        assert found[1] == 'posting URL'
    finally:
        service.close()


def test_unreadable_posting_settles_in_manual_queue(tmp_path):
    from application_service.repository import MemoryRepository
    from services.scout_reading import PostingReadFailure
    repository = MemoryRepository()
    app = repository.create_application('Example', 'Backend', 'https://example.com/closed', target_state='Discovered')
    config = json.loads(Path('config/pipelines.json').read_text())
    config.pop('discovery_seconds', None)
    config['discovery_workers'] = 0
    for spec in config['pipelines'].values():
        spec['workers'] = 0
    config['pipelines']['bookmark']['workers'] = 1
    path = tmp_path / 'pipelines.json'
    path.write_text(json.dumps(config))
    def unreadable(*args):
        raise PostingReadFailure('Exact posting is no longer available')
    registry = WorkloadRegistry()
    service = Orchestrator(repository, registry, unreadable, str(path))
    try:
        service.tick()
        for future in service.inflight:
            future.result(timeout=10)
        assert repository.get_application(app.application_id).status == 'Manual Apply Required'
        assert registry.snapshot()[0].status == 'succeeded'
    finally:
        service.close()
