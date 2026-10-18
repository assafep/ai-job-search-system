from persistence_api.repository import MemoryRepository
from services.workload_executor import execute_persistence_writes, plan_persistence_writes
from adapters.job_boards.posting_details import parse_saved_posting
import pytest
from dataclasses import asdict, is_dataclass


def test_bookmark_saves_posting_and_records_local_link(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: pytest.fail('bookmark must not fetch again'))
    source = {'company': 'Acme', 'role': 'Backend Engineer',
              'posting_url': 'https://jobs.example.test/1', 'description': 'verified ' * 100,
              'listing_url': 'https://source.example.test/listing'}
    operations = plan_persistence_writes('discover-and-bookmark', {'job_postings': [source]},
                                         {'job-discovery': {'candidates': [source]}})
    repository = MemoryRepository()
    execute_persistence_writes(operations, repository, dry_run=False)
    execute_persistence_writes(operations, repository, dry_run=False)
    application = repository.list_applications()[0]
    artifact = repository.list_artifacts(application.application_id)[0]
    assert artifact.name.startswith('Job Posting - ')
    saved = parse_saved_posting(repository.read_artifact(application.application_id, artifact.artifact_id)['content'])
    assert saved['description'] == source['description']
    assert saved['source_url'] == source['listing_url']
    assert len(repository.list_artifacts(application.application_id)) == 1
    assert any(event.event_type == 'posting_content_saved' for event in repository.list_events(application.application_id))


def test_missing_scout_content_does_not_fetch_or_bookmark(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: pytest.fail('must not fetch'))
    candidate = {'company': 'Acme', 'role': 'Backend', 'posting_url': 'https://example.test/job'}
    with pytest.raises(ValueError, match='matching scout source'):
        plan_persistence_writes('discover-and-bookmark', {}, {'job-discovery': {'candidates': [candidate]}})
    with pytest.raises(ValueError, match='incomplete'):
        plan_persistence_writes('discover-and-bookmark', {'job_postings': [candidate]},
                                {'job-discovery': {'candidates': [candidate]}})


def test_preparation_uses_the_exact_scout_content_without_network(monkeypatch, candidate_profile):
    from services.workflow_runtime import WorkflowRuntime
    from services.workload_registry import WorkloadRegistry
    from services.event_dispatcher import dispatch
    repository = MemoryRepository()
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
    operations = plan_persistence_writes('discover-and-bookmark', {'job_postings': [source]},
                                         {'job-discovery': {'candidates': [source]}})
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: pytest.fail('no repeat network reads'))
    execute_persistence_writes(operations, client, dry_run=False)
    application = repository.list_applications()[0]
    registry = WorkloadRegistry()
    envelope = dispatch('application_package_schedule', {'application_id': application.application_id}, registry=registry)

    def provider(prompt, context):
        assert context['job_posting']['description'] == source['description']
        return {'cover_letter': 'Dear Acme Hiring Team,\n\nI build reliable backends.', 'proposed_action': 'Proceed'}

    outcome = WorkflowRuntime(registry, client).run(envelope['workload_id'], agent_provider=provider)
    assert outcome['status'] == 'succeeded'
    assert application.status == 'Ready for Auto-Apply'
    artifacts = repository.list_artifacts(application.application_id)
    assert len(artifacts) == 2
    assert any(artifact.mime_type == 'application/pdf' for artifact in artifacts)
