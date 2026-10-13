from persistence_api.repository import MemoryRepository
from services.workload_executor import execute_persistence_writes
import services.workload_executor as executor


def test_bookmark_saves_posting_and_records_local_link(monkeypatch):
    monkeypatch.setattr(executor, 'fetch_posting_details', lambda url: {
        'posting_url': url, 'source_url': url, 'description': 'verified ' * 100,
    })
    repository = MemoryRepository()
    execute_persistence_writes([{
        'operation': 'create_application', 'company': 'Acme', 'role': 'Backend Engineer',
        'posting_url': 'https://jobs.example.test/1', 'capture_posting_content': True,
        'idempotency_key': 'bookmark-1',
    }], repository, dry_run=False)
    application = repository.list_applications()[0]
    artifact = repository.list_artifacts(application.application_id)[0]
    assert artifact.name.startswith('Job Posting - ')
    assert repository.read_artifact(application.application_id, artifact.artifact_id)['content']
    assert any(event.event_type == 'posting_content_saved' for event in repository.list_events(application.application_id))
