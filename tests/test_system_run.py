import json
from dataclasses import asdict

from persistence_api.repository import MemoryRepository
from services.event_dispatcher import dispatch
from services.workload_registry import WorkloadRegistry
from services import system_run


def test_system_run_submits_ready_snapshot_and_persists_complete_report(monkeypatch):
    repository = MemoryRepository()
    initial = repository.create_application('Initial', 'Role', 'https://example.test/1')
    repository.transition(initial.application_id, 'Ready for Auto-Apply')
    fresh = repository.create_application('Fresh', 'Role', 'https://example.test/2')
    registry = WorkloadRegistry()
    observed_submissions = []

    class Persistence:
        def list_applications(self, status=None):
            return [asdict(a) for a in repository.list_applications(status)]
        def get_application(self, app_id):
            return asdict(repository.get_application(app_id))
        def store_file(self, *args):
            return repository.store_file(*args)

    class Submission:
        def __init__(self, *args): pass
        def run_one(self, app_id):
            observed_submissions.append(app_id)
            repository.transition(app_id, 'Manual Apply Required')
            return {'status': 'Blocked', 'requires_human_verification': True}

    class Runtime:
        approval_registry = object()
        def run(self, workload_id, agent_provider=None):
            record = registry.claim(workload_id)
            if record.workflow == 'prepare-bookmarked':
                repository.transition(fresh.application_id, 'Ready for Auto-Apply')
            registry.complete(workload_id)
            return {'workflow': record.workflow, 'status': 'succeeded'}

    class Daemon:
        def __init__(self, *args): pass
        def enqueue_bookmark_preparation(self):
            dispatch('application_package_schedule', {'application_id': fresh.application_id}, 'fresh', registry)
        def enqueue_ready_submission(self):
            dispatch('application_submission_schedule', {'application_id': fresh.application_id}, 'fresh', registry)

    monkeypatch.setattr(system_run, 'ReadySubmission', Submission)
    monkeypatch.setattr(system_run, 'AutomationDaemon', Daemon)
    monkeypatch.setattr(system_run, 'emit', lambda result: None)
    monkeypatch.setattr('services.external_event_polling.poll', lambda p, r: {
        'handle-incoming-email': {'status': 'ok'}, 'prepare-interview': {'status': 'ok'}})
    result = system_run.run(Persistence(), registry, Runtime(), lambda p, c: {})
    assert observed_submissions == [initial.application_id]
    assert result['initial_ready_processed'] == 1
    assert result['initial_ready_final_states'][0]['status'] == 'Manual Apply Required'
    assert set(result['workload_counts']) == {'submit-ready', 'reconcile', 'discover-and-bookmark', 'prepare-bookmarked'}
    saved = json.loads(next(iter(repository.files.values()))['content'])
    assert saved['finished_at']
    assert saved['initial_ready_applications'][0]['application_id'] == initial.application_id
