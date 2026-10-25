import json
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from application_service.repository import MemoryRepository
from services.orchestrator import Orchestrator
from services.workload_registry import WorkloadRegistry
from services.polling import discovery_capacity


class RecordingPool:
    def __init__(self):
        self.calls = []
    def submit(self, function, *args):
        self.calls.append((function, args))
        return Future()
    def shutdown(self, **kwargs):
        pass


def service_with_pool(tmp_path, repository=None):
    config = json.loads(Path('config/pipelines.json').read_text())
    for spec in config['pipelines'].values():
        spec['workers'] = 0
    config['discovery_workers'] = 0
    path = tmp_path / 'pipelines.json'
    path.write_text(json.dumps(config))
    service = Orchestrator(repository or MemoryRepository(), WorkloadRegistry(), None, str(path))
    service.pool.shutdown()
    service.pool = RecordingPool()
    return service


def test_busy_pool_skips_work_and_released_slot_waits_until_next_minute(tmp_path):
    service = service_with_pool(tmp_path)
    service.pool_size = 1
    clock = [0]
    service.schedule.clock = lambda: clock[0]
    occupied = Future()
    service.inflight[occupied] = {'pipeline': 'busy', 'application_id': 'busy'}
    try:
        service.tick()
        assert service.pool.calls == []
        assert service.registry.snapshot() == []
        occupied.set_result(None)
        clock[0] = 59
        service.tick()
        assert service.pool.calls == []
        clock[0] = 60
        service.tick()
        assert len(service.pool.calls) == 1
        assert len(service.inflight) == 1
        # Remaining workflows must not queue behind the occupied slot.
        assert service.registry.snapshot() == []
        clock[0] = 119
        service.tick()
        assert len(service.pool.calls) == 1
    finally:
        service.close()


def test_discovery_shares_remaining_tab_budget_across_available_workers(tmp_path):
    class Journal:
        def journal_tabs(self):
            return [{}] * 97
    service = service_with_pool(tmp_path, Journal())
    service.config['discovery_workers'] = 7
    service.pool_size = 2
    try:
        service._maybe_discover()
        assert len(service.pool.calls) == 2
        assert sum(args[2] for _, args in service.pool.calls) == 3
        assert service.discovery_max_postings == 3
        assert sorted(source for _, args in service.pool.calls for source in args[1]) == sorted(service.config['discovery_sources'])
    finally:
        service.close()


def test_full_journal_skips_discovery_without_calling_sources(tmp_path):
    class Journal:
        def journal_tabs(self):
            return [{}] * 104
    service = service_with_pool(tmp_path, Journal())
    service.config['discovery_workers'] = 7
    try:
        service._maybe_discover()
        assert service.pool.calls == []
        assert service.discovery_max_postings == 0
    finally:
        service.close()


def test_concurrent_discovery_workers_cannot_exceed_total_tab_limit(tmp_path):
    repository = MemoryRepository()
    for index in range(97 - len(repository.journal_tabs())):
        repository.create_application('Seed', 'Role', 'https://example.test/seed/' + str(index))
    service = service_with_pool(tmp_path, repository)
    def postings(profile, sources, maximum):
        return [{'company': sources[0] + str(index), 'role': 'Role',
                 'posting_url': 'https://example.test/' + sources[0] + '/' + str(index)}
                for index in range(10)]
    service._collect_postings = postings
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(service._run_discovery, 1, ['a'], 2),
                       pool.submit(service._run_discovery, 2, ['b'], 1)]
            for future in futures:
                future.result(timeout=5)
        assert len(repository.journal_tabs()) == 100
        assert discovery_capacity(repository) == 0
        assert len(repository.list_applications('Discovered')) == 3
        # The service boundary also rejects callers that bypass the scheduler.
        assert repository.create_application('Overflow', 'Role', 'https://example.test/overflow',
                                             target_state='Discovered') is None
    finally:
        service.close()
