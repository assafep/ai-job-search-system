import json
from dataclasses import asdict

from adapters.job_boards.posting_details import posting_content_bytes
from application_service.repository import MemoryRepository
from services.dlq_url_repair import is_url_read_failure, repair_once, previous_state
from services.pipeline_agents import PipelineAgents
from services.scout_reading import PostingReadFailure


URL = 'https://jobs.example.test/blocked/123'
DESCRIPTION = 'Verified posting text. ' * 40


def dead_lettered_repository():
    repository = MemoryRepository()
    app = repository.create_application('Example', 'Backend Engineer', URL, target_state='Discovered')
    repository.transition(app.application_id, 'Dead Letters Queue', {
        'source': 'read-posting agent',
        'bullets': ['full job posting unavailable: ' + URL],
    }, 'dead-letter-read')
    return repository, app


def test_repair_restores_pre_dead_letter_state_and_is_idempotent(monkeypatch):
    repository, app = dead_lettered_repository()
    monkeypatch.setattr(
        'services.dlq_url_repair.fetch_posting_details',
        lambda url: {'posting_url': url, 'source_url': url, 'description': DESCRIPTION})

    result = repair_once(repository)
    assert result == [{'application_id': app.application_id, 'status': 'repaired',
                       'restored_state': 'Discovered'}]
    assert app.status == 'Discovered'
    assert len(repository.list_artifacts(app.application_id)) == 1
    saved = repository.artifact_contents[repository.list_artifacts(app.application_id)[0].artifact_id]
    assert json.loads(saved)['description'] == DESCRIPTION
    assert repair_once(repository) == []


def test_previous_state_uses_transition_event():
    repository, app = dead_lettered_repository()
    assert previous_state(repository.list_events(app.application_id)) == 'Discovered'


def test_system_error_that_mentions_reader_is_not_url_failure():
    repository = MemoryRepository()
    app = repository.create_application('Example', 'Backend Engineer', URL, target_state='Discovered')
    repository.transition(app.application_id, 'Dead Letters Queue', {
        'source': 'bookmark stage',
        'bullets': [
            'Pipeline: bookmark', 'Agent: read-posting', 'Outcome: system_error',
            'error: Application Service returned 404',
        ],
    }, 'dead-letter-system')
    assert repair_once(repository) == [
        {'application_id': app.application_id, 'status': 'skipped',
         'reason': 'not a URL read failure'}]
    assert app.status == 'Dead Letters Queue'


def test_empty_read_failure_field_from_successful_read_is_not_url_failure():
    repository = MemoryRepository()
    app = repository.create_application('Example', 'Backend Engineer', URL, target_state='Discovered')
    repository.transition(app.application_id, 'Bookmarked', {
        'source': 'bookmark stage',
        'bullets': [
            'Agent: read-posting', 'Outcome: read',
            'read failure:', 'resolved url: ' + URL,
        ],
    }, 'successful-read')
    repository.transition(app.application_id, 'Dead Letters Queue', {
        'source': 'validate stage',
        'bullets': [
            'Outcome: system_error',
            'error: saved posting content belongs to another position',
        ],
    }, 'dead-letter-validation')
    assert is_url_read_failure(repository.list_events(app.application_id)) is False
    assert repair_once(repository) == [
        {'application_id': app.application_id, 'status': 'skipped',
         'reason': 'not a URL read failure'}]


def test_url_read_does_not_call_the_model(monkeypatch):
    repository = MemoryRepository()
    app = repository.create_application('Example', 'Backend Engineer', URL, target_state='Discovered')
    repository.profile = {'schema_version': 1, 'personal': {}, 'professional': {},
                          'employment': [], 'scheduling': {}}
    agents = PipelineAgents(repository, None, lambda *_: None)
    monkeypatch.setattr(
        'services.bookmark_read.fetch_posting_details',
        lambda url: {'posting_url': url, 'source_url': url, 'description': DESCRIPTION})

    result = agents({'agent': 'read-posting'}, asdict(app), 'work-1')
    assert result['outcome'] == 'read'
    assert 'attempt' not in result
    artifact = result['artifacts'][0]
    assert json.loads(__import__('base64').b64decode(artifact['content_base64']))['description'] == DESCRIPTION


def test_url_failure_retries_with_the_language_model(monkeypatch):
    repository = MemoryRepository()
    app = repository.create_application('Legit Security', 'Software Eng Team Lead', URL, target_state='Discovered')
    repository.profile = {'schema_version': 1, 'personal': {}, 'professional': {},
                          'employment': [], 'scheduling': {}}
    agents = PipelineAgents(repository, None, lambda *_: None)

    def unavailable(url):
        raise ValueError('HTTP 403')

    monkeypatch.setattr('services.bookmark_read.fetch_posting_details', unavailable)
    monkeypatch.setattr('services.bookmark_read.run_agent', lambda *args: {
        'company': 'Legit Security', 'role': 'Software Eng Team Lead', 'posting_url': URL,
        'description': DESCRIPTION, 'read_failure': '',
    })
    monkeypatch.setattr('services.bookmark_read.configured_reader', lambda provider=None: (provider, 'grok-4.7', 'medium'))

    result = agents({'agent': 'read-posting'}, asdict(app), 'work-1')
    assert result['outcome'] == 'read'
    text = '\n'.join(result['attempt']['logic'])
    assert 'Trying again with the language model' in text
    assert result['attempt']['model'] == 'grok-4.7'
    assert result['attempt']['effort'] == 'medium'
    assert result['attempt']['obstacles'] == ['HTTP 403']


def test_url_and_model_failure_records_the_retry(monkeypatch):
    repository = MemoryRepository()
    app = repository.create_application('Legit Security', 'Software Eng Team Lead', URL, target_state='Discovered')
    repository.profile = {'schema_version': 1, 'personal': {}, 'professional': {},
                          'employment': [], 'scheduling': {}}
    agents = PipelineAgents(repository, None, lambda *_: None)

    monkeypatch.setattr('services.bookmark_read.fetch_posting_details', lambda url: (_ for _ in ()).throw(ValueError('HTTP 403')))
    monkeypatch.setattr('services.bookmark_read.configured_reader', lambda provider=None: (provider, 'grok-4.7', 'medium'))

    def stop(*_args):
        raise RuntimeError('max turns reached')

    monkeypatch.setattr('services.bookmark_read.run_agent', stop)
    try:
        agents({'agent': 'read-posting'}, asdict(app), 'work-1')
    except PostingReadFailure as error:
        assert 'HTTP 403' in str(error)
        assert 'max turns reached' in str(error)
        assert 'Trying again with the language model' in ' '.join(error.attempt['logic'])
        assert error.attempt['model'] == 'grok-4.7'
        assert error.attempt['effort'] == 'medium'
    else:
        raise AssertionError('expected PostingReadFailure')
