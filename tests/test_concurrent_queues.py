from dataclasses import asdict
from threading import Barrier, RLock

from persistence_api.repository import MemoryRepository
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
        p.repository.transition(app.application_id, 'Ready for Auto-Apply')
    barrier = Barrier(8)
    class Submission:
        def __init__(self, *args): pass
        def execute(self, app_id, workload_id):
            barrier.wait(timeout=10)
            return {'status': 'Blocked', 'blockers': ['Test submission was blocked']}
    monkeypatch.setattr(concurrent_queues, 'ReadySubmission', Submission)
    report = concurrent_queues.run(p, WorkloadRegistry(), object(), lambda p, c: {})
    assert report['workers'] == report['peak_active_postings'] == 8
    assert len(report['worker_threads_used']) == 8
    assert report['processed_postings'] == 16
    assert len({r['application_id'] for r in report['posting_results']}) == 16
    assert report['workload_counts'] == {'submit-ready': 16}
    assert report['activity_report_pdf']['mime_type'] == 'application/pdf'


def test_prepared_posting_flows_into_submission_in_same_pool(monkeypatch):
    p = Persistence()
    app = p.repository.create_application('Example', 'Backend', 'https://example.test/job')
    class Runtime:
        def run(self, workload_id, agent_provider=None):
            p.repository.transition(app.application_id, 'Ready for Auto-Apply')
            return {'workflow': 'prepare-bookmarked', 'status': 'succeeded'}
    class Submission:
        def __init__(self, *args): pass
        def execute(self, id, workload_id):
            p.repository.transition(id, 'Applied')
            return {'status': 'Submitted successfully', 'confirmation_text': 'Application received'}
    monkeypatch.setattr(concurrent_queues, 'ReadySubmission', Submission)
    report = concurrent_queues.run(p, WorkloadRegistry(), Runtime(), lambda p, c: {})
    assert report['processed_postings'] == 1
    assert report['posting_results'][0]['final_state'] == 'Applied'
    assert report['workload_counts'] == {'prepare-bookmarked': 1, 'submit-ready': 1}
