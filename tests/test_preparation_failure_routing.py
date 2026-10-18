from dataclasses import asdict

import pytest

from persistence_api.repository import MemoryRepository
from services.event_dispatcher import dispatch
from services.workflow_runtime import WorkflowRuntime
from services.workload_registry import WorkloadRegistry


class Client:
    def __init__(self, profile):
        self.repository = MemoryRepository()
        self.repository.save_profile(profile)
        self.application = self.repository.create_application('Example', 'Backend', 'https://example.test/job')
    def get_application(self, id):
        app = self.repository.get_application(id)
        return asdict(app) if app else None
    def get_profile(self): return self.repository.get_profile()
    def list_artifacts(self, id): return [asdict(a) for a in self.repository.list_artifacts(id)]
    def read_artifact(self, *args): return self.repository.read_artifact(*args)
    def list_events(self, id): return [asdict(e) for e in self.repository.list_events(id)]
    def store_artifact(self, *args): return asdict(self.repository.store_artifact(*args))
    def append_event(self, *args): return asdict(self.repository.append_event(*args))
    def transition(self, *args): return asdict(self.repository.transition(*args))


def make_runtime(profile):
    client = Client(profile)
    from adapters.job_boards.posting_details import posting_content_bytes
    client.repository.store_artifact(client.application.application_id, 'Job Posting - Example - Backend.json',
                                    posting_content_bytes({'posting_url': client.application.posting_url,
                                                           'description': 'Verified original posting details. ' * 30}),
                                    'application/json')
    registry = WorkloadRegistry()
    envelope = dispatch('application_package_schedule', {'application_id': client.application.application_id}, 'failure-routing', registry)
    return client, registry, WorkflowRuntime(registry, client), envelope['workload_id']


@pytest.mark.parametrize('phase', ['retrieval', 'generation', 'storage', 'agent'])
def test_preparation_failures_move_to_dead_letters_with_reason(monkeypatch, candidate_profile, phase):
    client, registry, runtime, id = make_runtime(candidate_profile)
    def failure(*args, **kwargs): raise RuntimeError(phase + ' failed')
    monkeypatch.setattr('adapters.job_boards.posting_details.fetch_posting_details',
                        lambda *args: pytest.fail('preparation must not download the posting'))
    if phase == 'retrieval':
        client.read_artifact = failure
    if phase == 'generation':
        monkeypatch.setattr('services.workload_executor.generate_application_pdf', failure)
    if phase == 'storage':
        client.store_artifact = failure
    provider = failure if phase == 'agent' else lambda prompt, context: {'cover_letter': 'Dear Example Hiring Team,\n\nI build reliable backends.', 'proposed_action': 'Proceed'}
    with pytest.raises(RuntimeError, match=phase + ' failed'):
        runtime.run(id, agent_provider=provider)
    assert client.application.status == 'Dead Letters Queue'
    assert registry.get(id).status == 'failed'
    event = next(e for e in client.repository.list_events(client.application.application_id) if e.event_type == 'status_changed')
    assert phase + ' failed' in str(event.data['bullets'])
    assert event.data['data']['source'] == 'application package preparation agent'


def test_invalid_saved_package_routes_to_dead_letters_and_preserves_artifact(candidate_profile):
    client, registry, runtime, id = make_runtime(candidate_profile)
    client.repository.store_artifact(client.application.application_id,
                                    'Candidate Cover Letter & Resume - Example - Backend.pdf', b'not a PDF')
    with pytest.raises(Exception):
        runtime.run(id, agent_provider=lambda p, c: pytest.fail('invalid saved PDF must not be sent to agent'))
    assert client.application.status == 'Dead Letters Queue'
    assert len(client.repository.list_artifacts(client.application.application_id)) == 2


def test_retry_does_not_repeat_preparation_or_duplicate_failure_event(monkeypatch, candidate_profile):
    client, registry, runtime, id = make_runtime(candidate_profile)
    calls = []
    def failure(*args):
        calls.append(args)
        raise RuntimeError('Posting details unavailable')
    client.read_artifact = failure
    with pytest.raises(RuntimeError, match='Posting details unavailable'):
        runtime.run(id, agent_provider=lambda p, c: {})
    result = runtime.run(id, agent_provider=lambda p, c: pytest.fail('dead letters must not regenerate'))
    assert result['status'] == 'succeeded'
    assert len(calls) == 1
    assert len([e for e in client.repository.list_events(client.application.application_id) if e.event_type == 'status_changed']) == 1


def test_routing_failure_remains_visible_and_retry_reconciles_partial_move(monkeypatch, candidate_profile):
    client, registry, runtime, id = make_runtime(candidate_profile)
    original = client.transition
    def interrupted(*args):
        original(*args)
        raise RuntimeError('Transition response lost')
    client.transition = interrupted
    def failure(url): raise RuntimeError('Posting unavailable')
    monkeypatch.setattr('adapters.job_boards.posting_details.fetch_posting_details', failure)
    with pytest.raises(RuntimeError, match='Recording Dead Letters Queue also failed'):
        runtime.run(id, agent_provider=lambda p, c: {})
    assert registry.get(id).status == 'failed'
    client.transition = original
    runtime.run(id, agent_provider=lambda p, c: pytest.fail('must reconcile without regenerating'))
    assert client.application.status == 'Dead Letters Queue'
    assert len([e for e in client.repository.list_events(client.application.application_id) if e.event_type == 'status_changed']) == 1


def test_failure_handler_does_not_recreate_deleted_or_regress_submitted_applications(candidate_profile):
    client, registry, runtime, id = make_runtime(candidate_profile)
    record = registry.get(id)
    client.application.status = 'Applied'
    runtime._record_preparation_failure(record, RuntimeError('late failure'))
    assert client.application.status == 'Applied'
    client.repository.applications.pop(client.application.application_id)
    runtime._record_preparation_failure(record, RuntimeError('deleted posting'))
    assert client.repository.applications == {}
