from contextlib import contextmanager
from copy import deepcopy
from threading import RLock

import pytest

from application_service.workflow_runs import Runs, validate_request
from application_service.repository import MemoryRepository


class MemoryStore:
    def __init__(self):
        self.records = {}
        self.lock = RLock()

    @contextmanager
    def locked(self):
        with self.lock:
            value = deepcopy(self.records)
            yield value
            self.records = value


@pytest.fixture
def runs(monkeypatch):
    monkeypatch.setenv('AJSS_WORKFLOW_RUNS_ENABLED', '1')
    return Runs(MemoryStore())


def create(repository, number):
    return repository.create_application('Example ' + str(number), 'Engineer', 'https://example.test/' + str(number))


def test_scoped_admission_default_all_and_idempotency(runs):
    repository = MemoryRepository()
    first, second = create(repository, 1), create(repository, 2)
    request = {'workflow': 'validate-bookmarked', 'idempotency_key': 'all'}
    admitted = runs.start(request, repository)
    assert set(admitted['application_ids']) == {first.application_id, second.application_id}
    create(repository, 3)
    assert runs.start(request, repository) == admitted
    with pytest.raises(ValueError, match='already used'):
        runs.start(dict(request, limit=1), repository)
    selected = runs.start(dict(request, idempotency_key='specific', application_id=second.application_id), repository)
    assert selected['application_ids'] == [second.application_id]
    limited = runs.start(dict(request, idempotency_key='limited', limit=1), repository)
    assert len(limited['application_ids']) == 1
    assert repository.get_application(first.application_id).status == 'Bookmarked'


@pytest.mark.parametrize('data', [
    {}, {'workflow': 'missing', 'idempotency_key': 'a'},
    {'workflow': 'submit-prepared', 'idempotency_key': 'a', 'limit': True},
    {'workflow': 'submit-prepared', 'idempotency_key': 'a', 'limit': 0},
    {'workflow': 'submit-prepared', 'idempotency_key': 'a', 'limit': 1, 'application_id': 'a'},
    {'workflow': 'discover', 'idempotency_key': 'a', 'limit': 1},
    {'workflow': 'discover', 'idempotency_key': 'a', 'shell': 'rm'},
])
def test_invalid_requests(data):
    with pytest.raises(ValueError):
        validate_request(data)


def test_claims_serialize_workers_and_never_reclaim(runs):
    repository = MemoryRepository()
    first = runs.start({'workflow': 'discover', 'idempotency_key': 'a'}, repository)
    runs.start({'workflow': 'discover', 'idempotency_key': 'b'}, repository)
    claim = runs.claim('one')['run']
    assert claim['run_id'] == first['run_id']
    assert 'claim_token' not in runs.get(first['run_id'])
    assert runs.claim('two') == {'run': None}
    with pytest.raises(ValueError, match='Invalid worker claim'):
        runs.update(first['run_id'], {'claim_token': 'wrong', 'status': 'succeeded'})
    runs.update(first['run_id'], {'claim_token': claim['claim_token'], 'status': 'reconciliation_required'})
    assert runs.claim('two') == {'run': None}
    with pytest.raises(ValueError):
        runs.reconcile(first['run_id'], {'note': 'checked'})
    runs.reconcile(first['run_id'], {'worker_stopped': True, 'note': 'Receipts checked and original worker stopped'})
    assert runs.claim('two')['run']['run_id'] != first['run_id']
    assert runs.start({'workflow': 'discover', 'idempotency_key': 'a'}, repository)['status'] == 'failed'


def test_disabled_and_ineligible(runs, monkeypatch):
    repository = MemoryRepository()
    app = create(repository, 1)
    with pytest.raises(ValueError, match='not eligible'):
        runs.start({'workflow': 'submit-prepared', 'application_id': app.application_id, 'idempotency_key': 'a'}, repository)
    monkeypatch.delenv('AJSS_WORKFLOW_RUNS_ENABLED')
    with pytest.raises(RuntimeError, match='disabled'):
        runs.start({'workflow': 'discover', 'idempotency_key': 'b'}, repository)


def test_run_single_filters_before_execution(monkeypatch):
    from services import run_single
    calls = []
    monkeypatch.setattr(run_single, 'run_all', lambda *a, **kw: calls.append((a, kw)) or [])
    run_single.run('Prepared', application_id='target')
    assert calls[0][1]['application_id'] == 'target'


def test_worker_finishes_and_does_not_retry_uncertain_claim(monkeypatch):
    from services.chat_workflow_worker import consume_once
    class Client:
        def __init__(self):
            self.calls = []
        def _request(self, method, path, body):
            self.calls.append((path, body))
            if path.endswith('/claim'):
                return {'run': dict(run_id='chat-test', claim_token='secret', started_at='now')}
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
    from services import workflow_runner
    from unittest.mock import Mock
    persistence = Mock()
    persistence.list_applications.return_value = [{'application_id': 'other', 'status': 'Prepared'}]
    monkeypatch.setattr('services.runtime_config.load', lambda **kw: {})
    monkeypatch.setattr(workflow_runner, 'ApplicationServiceClient', lambda: persistence)
    monkeypatch.setattr(workflow_runner, 'registry_from_environment', lambda: object())
    monkeypatch.setattr(workflow_runner, 'runtime_from_environment', lambda **kw: Mock())
    with pytest.raises(ValueError, match='not found'):
        workflow_runner.run_all('Prepared', application_id='target', agent_provider=lambda *a: {})
    persistence.store_file.assert_not_called()


def test_http_and_mcp_workflow_tools(monkeypatch):
    import asyncio
    import base64
    import threading
    from http.server import HTTPServer
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from application_service import main, workflow_runs
    monkeypatch.setenv('REPOSITORY_BACKEND', 'memory')
    monkeypatch.setenv('APPLICATION_SERVICE_TOKEN', 'test-workflow-token')
    monkeypatch.setenv('AJSS_WORKFLOW_RUNS_ENABLED', '1')
    store = MemoryStore()
    monkeypatch.setattr(workflow_runs, 'Store', lambda: store)
    repository = MemoryRepository()
    monkeypatch.setattr(main, 'repository', repository)
    app = create(repository, 1)
    server = HTTPServer(('127.0.0.1', 0), main.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port)
    try:
        with httpx.Client(base_url=url, headers={'Authorization': 'Bearer test-workflow-token'}) as client:
            assert httpx.get(url + '/workflows').status_code == 401
            request = {'workflow': 'validate-bookmarked', 'application_id': app.application_id, 'idempotency_key': 'http'}
            response = client.post('/workflow-runs', json=request)
            assert response.status_code == 202
            run = response.json()
            assert run['application_ids'] == [app.application_id]
            assert client.post('/workflow-runs', json=request).json() == run
            claim = client.post('/workflow-runs/claim', json={'worker_id': 'test'}).json()['run']
            assert 'claim_token' not in client.get('/workflow-runs/' + run['run_id']).json()
            repository.store_file('test/report.pdf', b'%PDF-test', 'application/pdf')
            assert client.post('/workflow-runs/' + run['run_id'] + '/worker-update', json={
                'claim_token': claim['claim_token'], 'status': 'succeeded', 'report': {'path': 'test/report.pdf'}}).status_code == 200
            report = client.get('/workflow-runs/' + run['run_id'] + '/report').json()
            assert base64.b64decode(report['content_base64']) == b'%PDF-test'
        async def check():
            async with streamablehttp_client(url + '/mcp', headers={'Authorization': 'Bearer test-workflow-token'}) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    assert {'listWorkflows', 'startWorkflow', 'getWorkflowRun', 'getWorkflowRunReport'} <= names
                    assert not names & {'claimWorkflowRun', 'updateWorkflowRun', 'reconcileWorkflowRun'}
                    result = await session.call_tool('startWorkflow', {'body': dict(request, idempotency_key='mcp')})
                    assert not result.isError
                    assert result.structuredContent['data']['application_ids'] == [app.application_id]
                    invalid = await session.call_tool('startWorkflow', {'body': dict(request, idempotency_key='bad', limit=0)})
                    assert invalid.isError
        asyncio.run(check())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_workflow_only_catalog():
    from application_service.mcp_adapter import Catalog
    catalog = Catalog(profile='workflows')
    names = {tool.name for tool in catalog.tools}
    assert 'startWorkflow' in names
    assert 'transitionApplication' not in names
    assert 'deleteApplication' not in names
    assert 'claimWorkflowRun' not in names


def test_concurrent_claims_and_duplicate_starts(runs):
    from concurrent.futures import ThreadPoolExecutor
    repository = MemoryRepository()
    request = {'workflow': 'discover', 'idempotency_key': 'same'}
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = list(pool.map(lambda _: runs.start(request, repository), range(16)))
        claims = list(pool.map(lambda number: runs.claim(str(number)), range(16)))
    assert len({r['run_id'] for r in admitted}) == 1
    assert sum(c['run'] is not None for c in claims) == 1


def test_queue_executor_respects_snapshot_and_correct_workflow(monkeypatch):
    from services import concurrent_queues
    from services.workload_registry import WorkloadRegistry
    from tests.test_concurrent_queues import Persistence
    persistence = Persistence()
    first, second = create(persistence.repository, 1), create(persistence.repository, 2)
    persistence.repository.transition(first.application_id, 'Prepared')
    persistence.repository.transition(second.application_id, 'Prepared')
    calls = []
    def run_one(p, registry, runtime, application, **kwargs):
        calls.append((application['application_id'], kwargs['workflow_name']))
        return dict(application_id=application['application_id'], workflow=kwargs['workflow_name'], status='succeeded', final_state='Applied')
    monkeypatch.setattr(concurrent_queues, 'run_one', run_one)
    concurrent_queues.run(persistence, WorkloadRegistry(), object(), None,
                         workflows=['submit-prepared'], application_ids=[second.application_id])
    assert calls == [(second.application_id, 'submit-prepared')]
    assert persistence.get_application(first.application_id)['status'] == 'Prepared'
