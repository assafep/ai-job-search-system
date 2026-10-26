from contextlib import contextmanager
from copy import deepcopy
from threading import RLock

import pytest

from application_service.stage_tasks import StageTasks, validate_request
from application_service.repository import MemoryRepository


class MemoryStore:
    def __init__(self):
        self.records = {}
        self.lock = RLock()

    def snapshot(self):
        with self.lock:
            return deepcopy(self.records)

    @contextmanager
    def locked(self):
        with self.lock:
            value = deepcopy(self.records)
            yield value
            self.records = value


@pytest.fixture
def tasks(monkeypatch):
    monkeypatch.setenv('AJSS_STAGE_TASKS_ENABLED', '1')
    monkeypatch.setenv('AJSS_WORKSTATION_STAGES', '1')
    return StageTasks(MemoryStore())


def create(repository, number):
    return repository.create_application('Example ' + str(number), 'Engineer', 'https://example.test/' + str(number))


def test_scoped_admission_default_all_and_idempotency(tasks):
    repository = MemoryRepository()
    first, second = create(repository, 1), create(repository, 2)
    request = {'stage': 'validate-bookmarked', 'idempotency_key': 'all'}
    admitted = tasks.start(request, repository)
    assert set(admitted['application_ids']) == {first.application_id, second.application_id}
    create(repository, 3)
    assert tasks.start(request, repository) == admitted
    with pytest.raises(ValueError, match='already used'):
        tasks.start(dict(request, limit=1), repository)
    selected = tasks.start(dict(request, idempotency_key='specific', application_id=second.application_id), repository)
    assert selected['application_ids'] == [second.application_id]
    limited = tasks.start(dict(request, idempotency_key='limited', limit=1), repository)
    assert len(limited['application_ids']) == 1
    assert repository.get_application(first.application_id).status == 'Bookmarked'


@pytest.mark.parametrize('data', [
    {}, {'stage': 'missing', 'idempotency_key': 'a'},
    {'stage': 'submit-prepared', 'idempotency_key': 'a', 'limit': True},
    {'stage': 'submit-prepared', 'idempotency_key': 'a', 'limit': 0},
    {'stage': 'submit-prepared', 'idempotency_key': 'a', 'limit': 1, 'application_id': 'a'},
    {'stage': 'discover', 'idempotency_key': 'a', 'limit': 1},
    {'stage': 'discover', 'idempotency_key': 'a', 'shell': 'rm'},
])
def test_invalid_requests(data):
    with pytest.raises(ValueError):
        validate_request(data)


def test_claims_serialize_workers_and_never_reclaim(tasks):
    repository = MemoryRepository()
    first = tasks.start({'stage': 'discover', 'idempotency_key': 'a'}, repository)
    tasks.start({'stage': 'discover', 'idempotency_key': 'b'}, repository)
    claim = tasks.claim('one')['task']
    assert claim['stage_task_id'] == first['stage_task_id']
    assert 'claim_token' not in tasks.get(first['stage_task_id'])
    assert tasks.claim('two') == {'task': None}
    with pytest.raises(ValueError, match='Invalid worker claim'):
        tasks.update(first['stage_task_id'], {'claim_token': 'wrong', 'status': 'succeeded'})
    tasks.update(first['stage_task_id'], {'claim_token': claim['claim_token'], 'status': 'reconciliation_required'})
    assert tasks.claim('two') == {'task': None}
    with pytest.raises(ValueError):
        tasks.reconcile(first['stage_task_id'], {'note': 'checked'})
    tasks.reconcile(first['stage_task_id'], {'worker_stopped': True, 'note': 'Receipts checked and original worker stopped'})
    assert tasks.claim('two')['task']['stage_task_id'] != first['stage_task_id']
    assert tasks.start({'stage': 'discover', 'idempotency_key': 'a'}, repository)['status'] == 'failed'


def test_disabled_and_ineligible(tasks, monkeypatch):
    repository = MemoryRepository()
    app = create(repository, 1)
    with pytest.raises(ValueError, match='not eligible'):
        tasks.start({'stage': 'submit-prepared', 'application_id': app.application_id, 'idempotency_key': 'a'}, repository)
    monkeypatch.delenv('AJSS_STAGE_TASKS_ENABLED')
    with pytest.raises(RuntimeError, match='disabled'):
        tasks.start({'stage': 'discover', 'idempotency_key': 'b'}, repository)


def test_list_runs_filters_paginates_and_does_not_mutate(tasks):
    repository = MemoryRepository()
    for number in range(5):
        tasks.start({'stage': 'discover', 'idempotency_key': str(number)}, repository)
    claim = tasks.claim('private-worker')['task']
    # Equal timestamps must still produce stable pages.
    for record in tasks.store.records.values():
        record['created_at'] = '2026-10-25T00:00:00+00:00'
    before = deepcopy(tasks.store.records)
    seen, cursor = [], None
    while True:
        page = tasks.list(limit=2, cursor=cursor)
        seen.extend(row['stage_task_id'] for row in page['tasks'])
        assert all('worker_id' not in row and 'claim_token' not in row for row in page['tasks'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert seen == sorted(before, reverse=True)
    assert tasks.list(status='running')['tasks'][0]['stage_task_id'] == claim['stage_task_id']
    assert len(tasks.list(status='queued', stage='discover')['tasks']) == 4
    assert tasks.list(stage='retired-stage') == {'tasks': [], 'next_cursor': None}
    assert tasks.store.records == before
    for arguments in ({'limit': 0}, {'limit': 201}, {'limit': True},
                      {'status': 'unknown'}, {'cursor': 'missing'}, {'stage': ''}):
        with pytest.raises(ValueError):
            tasks.list(**arguments)


def test_run_single_filters_before_execution(monkeypatch):
    from services import run_single
    calls = []
    monkeypatch.setattr(run_single, 'run_all', lambda *a, **kw: calls.append((a, kw)) or [])
    run_single.run('Prepared', application_id='target')
    assert calls[0][1]['application_id'] == 'target'


def test_worker_finishes_and_does_not_retry_uncertain_claim(monkeypatch):
    from services.stage_task_worker import consume_once
    class Client:
        def __init__(self):
            self.calls = []
        def _request(self, method, path, body):
            self.calls.append((path, body))
            if path.endswith('/claim'):
                return {'task': dict(stage_task_id='chat-test', claim_token='secret', started_at='now')}
            return {}
    client = Client()
    assert consume_once(client, 'worker', lambda r, p: {'workload_results': [], 'activity_report_pdf': {'path': 'a.pdf'}})
    assert client.calls[-1][1]['status'] == 'succeeded'
    def broken(r, p):
        raise RuntimeError('uncertain')
    monkeypatch.setattr('services.activity_report.publish', lambda *a: {'path': 'failure.pdf'})
    with pytest.raises(RuntimeError):
        consume_once(client, 'worker', broken)
    assert client.calls[-1][1]['status'] == 'reconciliation_required'


def test_run_all_rejects_missing_scope_before_provider_or_execution(monkeypatch):
    from services import stage_runner
    from unittest.mock import Mock
    persistence = Mock()
    persistence.list_applications.return_value = [{'application_id': 'other', 'status': 'Prepared'}]
    monkeypatch.setattr('services.runtime_config.load', lambda **kw: {})
    monkeypatch.setattr(stage_runner, 'ApplicationServiceClient', lambda: persistence)
    monkeypatch.setattr(stage_runner, 'registry_from_environment', lambda: object())
    monkeypatch.setattr(stage_runner, 'runtime_from_environment', lambda **kw: Mock())
    with pytest.raises(ValueError, match='not found'):
        stage_runner.run_all('Prepared', application_id='target', agent_provider=lambda *a: {})
    persistence.store_file.assert_not_called()


def test_http_and_mcp_stage_tools(monkeypatch):
    import asyncio
    import base64
    import threading
    from http.server import HTTPServer
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from application_service import main, stage_tasks
    monkeypatch.setenv('REPOSITORY_BACKEND', 'memory')
    monkeypatch.setenv('APPLICATION_SERVICE_TOKEN', 'test-stage-token')
    monkeypatch.setenv('AJSS_STAGE_TASKS_ENABLED', '1')
    monkeypatch.setenv('AJSS_WORKSTATION_STAGES', '1')
    store = MemoryStore()
    monkeypatch.setattr(stage_tasks, 'Store', lambda: store)
    repository = MemoryRepository()
    monkeypatch.setattr(main, 'repository', repository)
    app = create(repository, 1)
    app.status = 'Discovered'
    server = HTTPServer(('127.0.0.1', 0), main.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port)
    try:
        with httpx.Client(base_url=url, headers={'Authorization': 'Bearer test-stage-token'}) as client:
            assert httpx.get(url + '/stages').status_code == 401
            assert httpx.get(url + '/stage-tasks').status_code == 401
            assert client.get('/stage-tasks').json() == {'tasks': [], 'next_cursor': None}
            request = {'stage': 'bookmark-discovered', 'application_id': app.application_id, 'idempotency_key': 'http'}
            response = client.post('/stage-tasks', json=request)
            assert response.status_code == 202
            run = response.json()
            assert run['application_ids'] == [app.application_id]
            assert client.post('/stage-tasks', json=request).json() == run
            claim = client.post('/stage-tasks/claim', json={'worker_id': 'test'}).json()['task']
            listed = client.get('/stage-tasks', params={'status': 'running', 'limit': 1}).json()
            assert listed['tasks'] == [client.get('/stage-tasks/' + run['stage_task_id']).json()]
            for query in ('limit=0', 'limit=no', 'limit=201', 'status=bad', 'cursor=missing',
                          'stage=', 'status=running&status=queued', 'unknown=x'):
                assert client.get('/stage-tasks?' + query).status_code == 400
            assert 'claim_token' not in client.get('/stage-tasks/' + run['stage_task_id']).json()
            repository.store_file('test/report.pdf', b'%PDF-test', 'application/pdf')
            assert client.post('/stage-tasks/' + run['stage_task_id'] + '/worker-update', json={
                'claim_token': claim['claim_token'], 'status': 'succeeded', 'report': {'path': 'test/report.pdf'}}).status_code == 200
            report = client.get('/stage-tasks/' + run['stage_task_id'] + '/report').json()
            assert base64.b64decode(report['content_base64']) == b'%PDF-test'
        async def check():
            async with streamablehttp_client(url + '/mcp', headers={'Authorization': 'Bearer test-stage-token'}) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    assert {'listStages', 'listStageTasks', 'startStageTask', 'getStageTask', 'getStageTaskReport'} <= names
                    listed = await session.call_tool('listStageTasks', {'status': 'succeeded', 'limit': 1})
                    assert not listed.isError
                    assert listed.structuredContent['data']['tasks'][0]['stage_task_id'] == run['stage_task_id']
                    assert not names & {'claimStageTask', 'updateStageTask', 'reconcileStageTask'}
                    result = await session.call_tool('startStageTask', {'body': dict(request, idempotency_key='mcp')})
                    assert not result.isError
                    assert result.structuredContent['data']['application_ids'] == [app.application_id]
                    invalid = await session.call_tool('startStageTask', {'body': dict(request, idempotency_key='bad', limit=0)})
                    assert invalid.isError
        asyncio.run(check())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_stage_only_catalog():
    from application_service.mcp_adapter import Catalog
    catalog = Catalog(profile='stages')
    names = {tool.name for tool in catalog.tools}
    assert 'startStageTask' in names
    assert 'listStageTasks' in names
    assert 'transitionApplication' not in names
    assert 'deleteApplication' not in names
    assert 'claimStageTask' not in names


def test_concurrent_claims_and_duplicate_starts(tasks):
    from concurrent.futures import ThreadPoolExecutor
    repository = MemoryRepository()
    request = {'stage': 'discover', 'idempotency_key': 'same'}
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = list(pool.map(lambda _: tasks.start(request, repository), range(16)))
        claims = list(pool.map(lambda number: tasks.claim(str(number)), range(16)))
    assert len({r['stage_task_id'] for r in admitted}) == 1
    assert sum(c['task'] is not None for c in claims) == 1


def test_queue_executor_respects_snapshot_and_correct_stage(monkeypatch):
    from services import concurrent_queues
    from services.workload_registry import WorkloadRegistry
    from tests.test_concurrent_queues import Persistence
    persistence = Persistence()
    first, second = create(persistence.repository, 1), create(persistence.repository, 2)
    persistence.repository.transition(first.application_id, 'Prepared')
    persistence.repository.transition(second.application_id, 'Prepared')
    calls = []
    def run_one(p, registry, runtime, application, **kwargs):
        calls.append((application['application_id'], kwargs['stage_name']))
        return dict(application_id=application['application_id'], stage=kwargs['stage_name'], status='succeeded', final_state='Applied')
    monkeypatch.setattr(concurrent_queues, 'run_one', run_one)
    concurrent_queues.run(persistence, WorkloadRegistry(), object(), None,
                         stages=['submit-prepared'], application_ids=[second.application_id])
    assert calls == [(second.application_id, 'submit-prepared')]
    assert persistence.get_application(first.application_id)['status'] == 'Prepared'
