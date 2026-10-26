"""Native-browser protocol tests. Never invoke a browser, model or external API."""
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
import json

import pytest

from application_service.chatgpt_tasks import Sessions
from application_service.repository import MemoryRepository
from application_service.stage_tasks import StageTasks
from services.document_generator import generate_application_pdf
from tests.test_stage_tasks import MemoryStore


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('AJSS_STAGE_TASKS_ENABLED', '1')
    repository = MemoryRepository()
    repository.save_profile({'schema_version': 1, 'personal': {'name': 'Candidate'},
        'professional': {'summary': 'Backend engineer'}, 'employment': [{'company': 'Previous Employer'}]})
    app = repository.create_application('Example', 'Backend Engineer', 'https://example.test/job')
    tasks = StageTasks(MemoryStore())
    lock = RLock()
    @contextmanager
    def guard():
        with lock:
            yield
    def unexpected(*args, **kwargs):
        pytest.fail('ChatGPT browser tasks must never launch a server browser or model')
    monkeypatch.setattr('adapters.browser.submission.adapter_from_environment', unexpected)
    monkeypatch.setattr('services.worker_main.load_provider', unexpected)
    return repository, app, tasks, Sessions(repository, tasks, guard)


def admit(setup, name='validate-bookmarked'):
    repository, app, tasks, sessions = setup
    if name == 'submit-prepared':
        repository.transition(app.application_id, 'Prepared')
        pdf = generate_application_pdf('Dear Example Hiring Team, Backend Engineer', '<p>Candidate resume</p>' * 60)
        repository.store_artifact(app.application_id, 'Candidate Cover Letter & Resume - Example - Backend Engineer.pdf', pdf, 'application/pdf')
    record = tasks.start({'stage': name, 'idempotency_key': 'native-' + name}, repository)
    return record['stage_task_id'], sessions.next(record['stage_task_id'])


def command(task, key, **extra):
    return dict(task_id=task['task_id'], token=task['token'], idempotency_key=key, **extra)


def observation():
    return {'company': 'Example', 'role': 'Backend Engineer', 'posting_url': 'https://example.test/job',
            'resolved_url': 'https://example.test/job', 'description': 'Backend role with relevant engineering requirements. ' * 20}


def decision(match=True):
    return {'company': 'Example', 'role': 'Backend Engineer', 'posting_url': 'https://example.test/job',
            'is_match': match, 'relevance_evidence': ['Previous backend engineering experience matches the role.'] if match else [],
            'decline_reason': '' if match else 'The role requires experience absent from the applicant profile.'}


def test_worker_cannot_claim_native_browser_work_and_status_hides_capabilities(setup):
    repository, app, tasks, sessions = setup
    stage_task_id, task = admit(setup)
    assert task['execution_host'] == 'chatgpt'
    assert task['browser'] == 'chatgpt-native'
    assert task['contracts']['validate']['schema']
    assert tasks.claim('workstation') == {'task': None}
    status = tasks.get(stage_task_id)
    assert 'chatgpt' not in status
    assert task['token'] not in json.dumps(status)
    assert 'applicant_profile' not in json.dumps(status)


def test_validation_reuses_existing_plan_and_produces_pdf_with_exact_retry(setup):
    repository, app, tasks, sessions = setup
    stage_task_id, task = admit(setup)
    payload = command(task, 'validation', posting=observation(), decision=decision())
    result = sessions.validate(stage_task_id, payload)
    count = len(repository.list_events(app.application_id))
    assert sessions.validate(stage_task_id, payload) == result
    assert len(repository.list_events(app.application_id)) == count
    assert app.status == 'Validated'
    assert len(repository.list_artifacts(app.application_id)) == 1
    final = sessions.next(stage_task_id)
    assert final['complete'] and final['task']['status'] == 'succeeded'
    assert repository.read_file(final['task']['report']['path'])['content'].startswith(b'%PDF')
    with pytest.raises(ValueError, match='another result'):
        sessions.validate(stage_task_id, dict(payload, decision=decision(False)))


def test_validation_decline_uses_existing_completion_logic(setup):
    repository, app, tasks, sessions = setup
    stage_task_id, task = admit(setup)
    result = sessions.validate(stage_task_id, command(task, 'decline', posting=observation(), decision=decision(False)))
    assert result['result']['final_state'] == 'Invalidated'
    assert repository.get_application(app.application_id).status == 'Invalidated'


@pytest.mark.parametrize('change', ['wrong_url', 'missing_evidence', 'stale_context', 'wrong_token'])
def test_invalid_or_stale_browser_result_cannot_mutate_application(setup, change):
    repository, app, tasks, sessions = setup
    stage_task_id, task = admit(setup)
    payload = command(task, 'validation', posting=observation(), decision=decision())
    if change == 'wrong_url': payload['posting']['posting_url'] = 'https://other.test/job'
    if change == 'missing_evidence': payload['decision']['relevance_evidence'] = []
    if change == 'stale_context': repository.save_profile({'schema_version': 2, 'personal': {}, 'professional': {}, 'employment': []})
    if change == 'wrong_token': payload['token'] = 'wrong'
    with pytest.raises(ValueError): sessions.validate(stage_task_id, payload)
    assert app.status == 'Bookmarked'
    assert not repository.list_artifacts(app.application_id)


def test_submission_records_intent_before_browser_and_success_requires_evidence(setup):
    repository, app, tasks, sessions = setup
    stage_task_id, task = admit(setup, 'submit-prepared')
    package = sessions.package(stage_task_id, task['task_id'], task['token'])
    assert package['sha256'] == task['package_sha256']
    assert package['mime_type'] == 'application/pdf'
    payload = command(task, 'begin')
    assert sessions.begin_submission(stage_task_id, payload)['may_submit'] is True
    from services.ready_submission import ReadySubmission
    pending = json.loads(repository.read_file(ReadySubmission.receipt_path(app.application_id))['content'])
    assert pending['status'] == 'Submission unconfirmed'
    assert sessions.begin_submission(stage_task_id, payload)['may_submit'] is False
    with pytest.raises(ValueError, match='already started'):
        sessions.begin_submission(stage_task_id, command(task, 'begin-again'))
    with pytest.raises(ValueError, match='confirmation'):
        sessions.complete_submission(stage_task_id, command(task, 'missing', outcome={'status': 'Submitted successfully'}))
    result_payload = command(task, 'finish', outcome={'status': 'Submitted successfully',
        'confirmation_text': 'Your application was received', 'observed_url': 'https://example.test/thanks'})
    result = sessions.complete_submission(stage_task_id, result_payload)
    assert app.status == 'Applied'
    events = len(repository.list_events(app.application_id))
    assert sessions.complete_submission(stage_task_id, result_payload) == result
    assert len(repository.list_events(app.application_id)) == events
    assert sessions.next(stage_task_id)['task']['status'] == 'succeeded'


def test_unconfirmed_submission_routes_to_manual_and_blocks_new_runs(setup):
    repository, app, tasks, sessions = setup
    stage_task_id, task = admit(setup, 'submit-prepared')
    sessions.begin_submission(stage_task_id, command(task, 'begin'))
    sessions.complete_submission(stage_task_id, command(task, 'unknown', outcome={
        'status': 'Submission unconfirmed', 'blockers': ['Browser closed after clicking submit; no receipt observed.']}))
    assert app.status == 'Manual Apply Required'
    assert sessions.next(stage_task_id)['task']['status'] == 'reconciliation_required'
    assert tasks.claim('worker') == {'task': None}


def test_captcha_pause_is_durable_without_backend_fallback(setup):
    repository, app, tasks, sessions = setup
    stage_task_id, task = admit(setup)
    payload = command(task, 'captcha', reason='captcha', explanation='The site requires user verification.')
    response = sessions.pause(stage_task_id, payload)
    assert response['user_interaction_required']
    assert sessions.pause(stage_task_id, payload) == response
    resumed = sessions.next(stage_task_id)
    assert resumed['task_id'] == task['task_id'] and resumed['state'] == 'paused'
    assert app.status == 'Bookmarked'


def test_post_intent_persistence_failure_is_not_replayed(setup, monkeypatch):
    repository, app, tasks, sessions = setup
    stage_task_id, task = admit(setup)
    def fail(*args, **kwargs): raise RuntimeError('Lost persistence response')
    monkeypatch.setattr(repository, 'store_artifact', fail)
    payload = command(task, 'validation', posting=observation(), decision=decision())
    with pytest.raises(RuntimeError): sessions.validate(stage_task_id, payload)
    assert sessions.next(stage_task_id)['task']['status'] == 'reconciliation_required'
    with pytest.raises(ValueError, match='uncertain'):
        sessions.validate(stage_task_id, payload)


def test_omitted_limit_processes_all_admitted_items_one_by_one(setup):
    repository, app, tasks, sessions = setup
    other = repository.create_application('Example', 'Backend Engineer', 'https://example.test/other')
    stage_task_id, task = admit(setup)
    seen = []
    while not task.get('complete'):
        seen.append(task['application_id'])
        url = task['context']['application']['posting_url']
        posting, result = observation(), decision()
        posting['posting_url'] = result['posting_url'] = url
        sessions.validate(stage_task_id, command(task, 'validate-' + task['task_id'], posting=posting, decision=result))
        task = sessions.next(stage_task_id)
    assert set(seen) == {app.application_id, other.application_id}


def test_bad_saved_package_routes_to_dead_letters_before_browser(setup):
    repository, app, tasks, sessions = setup
    repository.transition(app.application_id, 'Prepared')
    run = tasks.start({'stage': 'submit-prepared', 'idempotency_key': 'no-package'}, repository)
    result = sessions.next(run['stage_task_id'])
    assert app.status == 'Dead Letters Queue'
    assert result['task']['status'] == 'failed'
    assert result['complete']


def test_existing_confirmed_receipt_is_reconciled_without_another_browser_attempt(setup):
    repository, app, tasks, sessions = setup
    repository.transition(app.application_id, 'Prepared')
    from services.ready_submission import ReadySubmission
    repository.store_file(ReadySubmission.receipt_path(app.application_id), json.dumps({
        'status': 'Submitted successfully', 'confirmation_text': 'Previously received'}).encode(), 'application/json')
    run = tasks.start({'stage': 'submit-prepared', 'idempotency_key': 'old-confirmation'}, repository)
    response = sessions.next(run['stage_task_id'])
    assert app.status == 'Applied'
    assert response['complete'] and response['task']['status'] == 'succeeded'


def test_http_mcp_native_context_and_result_round_trip(monkeypatch):
    import asyncio
    import threading
    from http.server import HTTPServer
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from application_service import main, stage_tasks, chatgpt_tasks
    monkeypatch.setenv('REPOSITORY_BACKEND', 'memory')
    monkeypatch.setenv('APPLICATION_SERVICE_TOKEN', 'native-browser-test-token')
    monkeypatch.setenv('AJSS_STAGE_TASKS_ENABLED', '1')
    monkeypatch.setenv('AJSS_MCP_TOOL_PROFILE', 'stages')
    repository = MemoryRepository()
    repository.save_profile({'schema_version': 1, 'personal': {'name': 'Candidate'}, 'professional': {}, 'employment': []})
    app = repository.create_application('Example', 'Backend Engineer', 'https://example.test/job')
    store = MemoryStore()
    monkeypatch.setattr(main, 'repository', repository)
    monkeypatch.setattr(stage_tasks, 'Store', lambda: store)
    real = chatgpt_tasks.Sessions
    lock = RLock()
    @contextmanager
    def guarded():
        with lock: yield
    monkeypatch.setattr(chatgpt_tasks, 'Sessions', lambda repo, tasks: real(repo, tasks, guarded))
    server = HTTPServer(('127.0.0.1', 0), main.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def run():
        async with streamablehttp_client('http://127.0.0.1:' + str(server.server_port) + '/mcp',
                headers={'Authorization': 'Bearer native-browser-test-token'}) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool('startStageTask', {'body': {'stage': 'validate-bookmarked', 'idempotency_key': 'native-http'}})
                assert not result.isError
                stage_task_id = result.structuredContent['data']['stage_task_id']
                context = await session.call_tool('nextChatGPTStageTask', {'stage_task_id': stage_task_id, 'body': {}})
                assert not context.isError
                task = context.structuredContent['data']
                result = await session.call_tool('completeChatGPTValidation', {'stage_task_id': stage_task_id,
                    'body': command(task, 'native-observation', posting=observation(), decision=decision())})
                assert not result.isError
                final = await session.call_tool('nextChatGPTStageTask', {'stage_task_id': stage_task_id, 'body': {}})
                assert final.structuredContent['data']['task']['status'] == 'succeeded'
                assert app.status == 'Validated'
    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
