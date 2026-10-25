from dataclasses import asdict

from services.workflow_runtime import WorkflowRuntime
from services.document_generator import generate_application_pdf
from services.workload_registry import WorkloadRegistry
from services.event_dispatcher import dispatch
from application_service.repository import MemoryRepository


def test_review_hold_does_not_gate_successfully_saved_package_or_call_llm():
    repository = MemoryRepository()
    app = repository.create_application('Example', 'Backend', 'https://example.test/job')
    app.status = 'Validated'
    package = generate_application_pdf('Dear Example Hiring Team, Backend', '<p>Resume content</p>' * 60)
    repository.store_artifact(app.application_id, 'Candidate Cover Letter & Resume - Example - Backend.pdf', package)

    class Client:
        def get_application(self, id): return asdict(repository.get_application(id))
        def get_profile(self): return {'source_resume': {'path': None}}
        def list_artifacts(self, id): return [asdict(a) for a in repository.list_artifacts(id)]
        def read_artifact(self, *args): return repository.read_artifact(*args)
        def list_events(self, id): return [asdict(e) for e in repository.list_events(id)]
        def append_event(self, *args): return repository.append_event(*args)
        def transition(self, *args): return asdict(repository.transition(*args))
    registry = WorkloadRegistry()
    workload = dispatch('application_package_schedule', {'application_id': app.application_id}, 'test-reuse', registry)
    def forbidden(*args): raise AssertionError('saved package must not be regenerated')
    result = WorkflowRuntime(registry, Client()).run(workload['workload_id'], agent_provider=forbidden)
    assert result['status'] == 'succeeded'
    assert repository.get_application(app.application_id).status == 'Prepared'
    assert len(repository.list_artifacts(app.application_id)) == 1
