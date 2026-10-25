from application_service.repository import MemoryRepository
from services.workload_executor import execute_persistence_writes, plan_persistence_writes
from adapters.job_boards.posting_details import parse_saved_posting
import pytest
from dataclasses import asdict, is_dataclass


def test_read_posting_saves_posting_artifact(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: pytest.fail('must not fetch again'))
    repository = MemoryRepository()
    app = repository.create_application('Acme', 'Backend Engineer', 'https://jobs.example.test/1', data={}, idempotency_key='test-1')
    app.status = 'Discovered'
    operations = plan_persistence_writes(
        'bookmark-discovered',
        {'application_id': app.application_id, 'posting_url': 'https://jobs.example.test/1'},
        {'read-posting': {
            'company': 'Acme', 'role': 'Backend Engineer',
            'posting_url': 'https://jobs.example.test/1',
            'description': 'verified ' * 100,
        }}
    )
    execute_persistence_writes(operations, repository, dry_run=False)
    artifacts = repository.list_artifacts(app.application_id)
    assert len(artifacts) == 1
    assert artifacts[0].name.startswith('Job Posting - ')


@pytest.mark.parametrize("portable", [False, True])
def test_preparation_uses_the_exact_scout_content_without_network(monkeypatch, candidate_profile, portable):
    from services.workflow_runtime import WorkflowRuntime
    from services.workload_registry import WorkloadRegistry
    from services.event_dispatcher import dispatch
    repository = MemoryRepository()
    if portable:
        import hashlib
        from pathlib import Path
        content = Path(candidate_profile['source_resume']['path']).read_bytes()
        repository.store_file('Applicant/resume.pdf', content, 'application/pdf')
        candidate_profile['source_resume'] = {'path': 'Applicant/resume.pdf', 'sha256': hashlib.sha256(content).hexdigest()}
    repository.save_profile(candidate_profile)

    class Client:
        def __getattr__(self, name):
            def invoke(*args, **kwargs):
                result = getattr(repository, name)(*args, **kwargs)
                if isinstance(result, list):
                    return [asdict(item) if is_dataclass(item) else item for item in result]
                return asdict(result) if is_dataclass(result) else result
            return invoke

    client = Client()
    source = {'company': 'Acme', 'role': 'Backend Engineer',
              'posting_url': 'https://jobs.example.test/1', 'description': 'Original verified backend requirements. ' * 30}
    # Create application in Discovered state, then read-posting keeps it there
    repository.create_application('Acme', 'Backend Engineer', 'https://jobs.example.test/1', data={}, idempotency_key='test-1')
    app = repository.list_applications()[0]
    app.status = 'Discovered'
    # Save posting artifact (read-posting transitions to Discovered, which is a no-op here)
    posting = {
        'posting_url': 'https://jobs.example.test/1',
        'source_url': 'https://jobs.example.test/1',
        'description': source['description'],
        'company': 'Acme',
        'role': 'Backend Engineer',
    }
    operations = plan_persistence_writes(
        'bookmark-discovered',
        {'application_id': app.application_id, 'posting_url': 'https://jobs.example.test/1'},
        {'read-posting': {
            'company': 'Acme', 'role': 'Backend Engineer',
            'posting_url': 'https://jobs.example.test/1',
            'description': source['description'],
        }}
    )
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: pytest.fail('no repeat network reads'))
    execute_persistence_writes(operations, client, dry_run=False)
    # bookmark transitions to Bookmarked; validate transitions to Validated
    repository.transition(app.application_id, 'Validated', {'source': 'validate'}, 'validate-1')

    registry = WorkloadRegistry()
    envelope = dispatch('application_package_schedule', {'application_id': app.application_id}, registry=registry)

    def provider(prompt, context):
        assert context['job_posting']['description'].strip() == source['description'].strip()
        assert 'source_resume_base64' not in context
        return {'cover_letter': 'Dear Acme Hiring Team,\n\nI build reliable backends.', 'proposed_action': 'Proceed'}

    outcome = WorkflowRuntime(registry, client).run(envelope['workload_id'], agent_provider=provider)
    assert outcome['status'] == 'succeeded'
    assert app.status == 'Prepared'
    artifacts = repository.list_artifacts(app.application_id)
    assert len(artifacts) == 2
    assert any(artifact.mime_type == 'application/pdf' for artifact in artifacts)
