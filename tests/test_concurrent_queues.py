from dataclasses import asdict
from threading import Barrier, RLock

from application_service.repository import MemoryRepository
from services import concurrent_queues
from services.synchronization import synchronized
from services.workload_registry import WorkloadRegistry


class Persistence:
    def __init__(self):
        self._lock = RLock()
        self.repository = MemoryRepository()
    @synchronized
    def list_applications(self): return [asdict(a) for a in self.repository.list_applications()]
    @synchronized
    def get_application(self, id): return asdict(self.repository.get_application(id))
    @synchronized
    def store_file(self, *args): return self.repository.store_file(*args)
    def storage_statistics(self): return {'peak_concurrent_requests': 1, 'active_requests': 0}


def test_runner_uses_all_eight_threads_and_consumes_ready_queue_once(monkeypatch):
    p = Persistence()
    for index in range(16):
        app = p.repository.create_application('Company ' + str(index), 'Backend', 'https://example.test/' + str(index))
        p.repository.transition(app.application_id, 'Prepared')
    barrier = Barrier(8)
    original_run_one = concurrent_queues.run_one
    def fake_run_one(persistence, registry, runtime, application, **kwargs):
        barrier.wait(timeout=10)
        return {
            "application_id": application["application_id"],
            "stage": "submit-prepared",
            "initial_state": application["status"],
            "final_state": application["status"],
            "status": "succeeded",
        }
    monkeypatch.setattr(concurrent_queues, 'run_one', fake_run_one)
    report = concurrent_queues.run(p, WorkloadRegistry(), object(), lambda p, c: {})
    assert report['workers'] == 8
    assert report['processed_postings'] == 16
    assert len({r['application_id'] for r in report['posting_results']}) == 16
    assert report['workload_counts'] == {'submit-prepared': 16}
    assert report['activity_report_pdf']['mime_type'] == 'application/pdf'


def test_prepared_posting_flows_into_submission_in_same_pool(monkeypatch):
    p = Persistence()
    app = p.repository.create_application('Example', 'Backend', 'https://example.test/job')
    p.repository.transition(app.application_id, 'Prepared')
    def fake_run_one(persistence, registry, runtime, application, **kwargs):
        app_id = application["application_id"]
        persistence.repository.get_application(app_id)  # Verify it exists
        return {
            "application_id": app_id,
            "stage": "submit-prepared",
            "initial_state": "Prepared",
            "final_state": "Applied",
            "status": "succeeded",
        }
    monkeypatch.setattr(concurrent_queues, 'run_one', fake_run_one)
    report = concurrent_queues.run(p, WorkloadRegistry(), object(), lambda p, c: {})
    assert report['processed_postings'] == 1
    assert report['posting_results'][0]['final_state'] == 'Applied'
    assert report['workload_counts'] == {'submit-prepared': 1}


def test_discover_stages_from_blueprints(monkeypatch):
    """Verify that queue-driven stages are discovered from blueprints."""
    stages = concurrent_queues._discover_queue_stages()
    # At minimum, the core pipeline states should be present
    assert 'Discovered' in stages
    assert 'Bookmarked' in stages
    assert 'Validated' in stages
    assert 'Prepared' in stages
    # Each maps to a stage name and event type
    for state, info in stages.items():
        assert 'stage' in info
        assert 'event_type' in info
        assert 'outcomes' in info


def test_run_one_rechecks_state(monkeypatch):
    """Verify that run_one re-reads state before running (single-consumer pattern)."""
    from services.stage_runner import run_one

    p = Persistence()
    app = p.repository.create_application('LockTest', 'Backend', 'https://example.test/lock')
    p.repository.transition(app.application_id, 'Bookmarked')
    app_dict = asdict(p.repository.get_application(app.application_id))

    get_call_count = [0]
    original_get = p.get_application

    def fake_get_application(app_id):
        get_call_count[0] += 1
        return original_get(app_id)

    p.get_application = fake_get_application

    class FakeRuntime:
        def prepare_context(self, stage, context, agent_provider=None):
            return context
        def run_agent(self, stage, enriched_context, agent_provider):
            return {'validate': {'is_match': True, 'relevance_evidence': ['test']}}

    result = run_one(
        p, WorkloadRegistry(), FakeRuntime(), app_dict,
        stage_name='validate-bookmarked',
    )
    # get_application is called at least twice: once to re-check state,
    # and once after persistence writes to read the final state
    assert get_call_count[0] >= 2, f"Expected >= 2 get_application calls, got {get_call_count[0]}"
    assert result['application_id'] == app.application_id


def test_run_one_skips_state_changed(monkeypatch):
    """Verify that run_one skips if another worker already changed the state."""
    from services.stage_runner import run_one

    p = Persistence()
    app = p.repository.create_application('SkipTest', 'Backend', 'https://example.test/skip')
    p.repository.transition(app.application_id, 'Bookmarked')
    app_dict = asdict(p.repository.get_application(app.application_id))

    # Transition to Validated before run_one re-reads state
    p.repository.transition(app.application_id, 'Validated')

    class FakeRuntime:
        def prepare_context(self, stage, context, agent_provider=None):
            return context
        def run_agent(self, stage, enriched_context, agent_provider):
            raise AssertionError("Agent should not be called when state changed")

    result = run_one(
        p, WorkloadRegistry(), FakeRuntime(), app_dict,
        stage_name='validate-bookmarked',
    )
    assert result['status'] == 'skipped'
    assert result['reason'] == 'state changed by another worker'
