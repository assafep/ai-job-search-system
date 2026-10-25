"""Managed-queue planning and no-replay tests using isolated memory state."""
from contextlib import contextmanager
from threading import RLock

import pytest

from application_service.repository import MemoryRepository
from application_service.runtime_client import RuntimeClient
from application_service.vercel_workflows import Engine, ConfigurationRequired, Busy
from application_service.workflow_runs import Runs
from tests.test_chat_workflow_runs import MemoryStore


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('AJSS_WORKFLOW_RUNS_ENABLED', '1')
    monkeypatch.setenv('AJSS_VERCEL_WORKFLOWS_ENABLED', '1')
    repository = MemoryRepository()
    first = repository.create_application('Example', 'Engineer', 'https://example.test/1', target_state='Discovered')
    second = repository.create_application('Other', 'Engineer', 'https://example.test/2', target_state='Discovered')
    runs = Runs(MemoryStore())
    record = runs.start({'workflow': 'bookmark-discovered', 'idempotency_key': 'cloud'}, repository)
    lock = RLock()
    @contextmanager
    def guard():
        with lock: yield
    published, calls = [], []
    def publisher(run, unit): published.append((run, unit))
    def execute(record, unit):
        calls.append(unit['unit_id'])
        return {'workflow': 'bookmark-discovered', 'application_id': unit['application_id'], 'status': 'succeeded'}
    engine = Engine(runs, guard, publisher, execute, RuntimeClient(repository))
    return repository, runs, record, engine, published, calls


def test_cloud_fans_out_entire_scope_and_never_uses_workstation(setup):
    repository, runs, record, engine, published, calls = setup
    assert record['execution_host'] == 'vercel'
    assert runs.claim('workstation') == {'run': None}
    engine.plan(record['run_id'])
    assert len(published) == 2
    for run, unit in published.copy(): engine.consume(run, unit)
    status = runs.get(record['run_id'])
    assert status['status'] == 'succeeded'
    assert status['progress'] == {'admitted': 2, 'completed': 2}
    assert 'vercel' not in status
    assert repository.read_file(status['report']['path'])['content'].startswith(b'%PDF')
    engine.consume(*published[0])
    assert len(calls) == 2


def test_uncertain_unit_never_replays_business_effects(setup):
    repository, runs, record, engine, published, calls = setup
    engine.plan(record['run_id'])
    def interrupted(record, unit):
        calls.append(unit['unit_id'])
        raise RuntimeError('Lost response after an effect')
    engine.executor = interrupted
    with pytest.raises(RuntimeError): engine.consume(*published[0])
    engine.consume(*published[0])
    assert len(calls) == 1
    assert runs.get(record['run_id'])['status'] == 'reconciliation_required'


def test_redelivery_after_process_death_marks_reconciliation_required(setup):
    repository, runs, record, engine, published, calls = setup
    engine.plan(record['run_id'])
    with runs.store.locked() as records:
        records[record['run_id']]['status'] = 'running'
        records[record['run_id']]['vercel']['units'][published[0][1]]['status'] = 'running'
    engine.consume(*published[0])
    assert calls == []
    assert runs.get(record['run_id'])['status'] == 'reconciliation_required'


def test_missing_provider_never_claims_a_posting_or_creates_effects(setup):
    repository, runs, record, engine, published, calls = setup
    engine.plan(record['run_id'])
    def unavailable(record, unit): raise ConfigurationRequired('Configure a model credential')
    engine.executor = unavailable
    with pytest.raises(ConfigurationRequired): engine.consume(*published[0])
    status = runs.get(record['run_id'])
    assert status['status'] == 'queued'
    assert status['requirements'] == ['Configure a model credential']
    assert all(app.status == 'Discovered' for app in repository.list_applications())


def test_cloud_never_executes_chatgpt_browser_workflow(setup):
    repository, runs, record, engine, published, calls = setup
    app = repository.create_application('Browser', 'Engineer', 'https://example.test/native')
    native = runs.start({'workflow': 'validate-bookmarked', 'idempotency_key': 'native'}, repository)
    with pytest.raises(ValueError, match='belong to Vercel'): engine.plan(native['run_id'])
    assert calls == []


def test_managed_queue_admission_retry_republishes_missing_units(setup):
    repository, runs, record, engine, published, calls = setup
    def interrupted(run, unit):
        published.append((run, unit))
        raise ConnectionError
    engine.publisher = interrupted
    with pytest.raises(ConnectionError): engine.plan(record['run_id'])
    engine.publisher = lambda run, unit: published.append((run, unit))
    engine.plan(record['run_id'])
    assert len(set(published)) == 2
    assert calls == []


def test_active_native_run_prevents_parallel_cloud_claim(setup):
    repository, runs, record, engine, published, calls = setup
    engine.plan(record['run_id'])
    with runs.store.locked() as records:
        records['native'] = {'run_id': 'native', 'status': 'running'}
    with pytest.raises(Busy): engine.consume(*published[0])
    assert calls == []


def test_discovery_plans_one_unit_per_configured_board(setup):
    repository, runs, record, engine, published, calls = setup
    discovery = runs.start({'workflow': 'discover', 'idempotency_key': 'sources'}, repository)
    engine.plan(discovery['run_id'])
    units = engine._load(discovery['run_id'])['vercel']['units'].values()
    assert any(unit['payload'].get('discovery_board') for unit in units)
    assert all(len(unit['payload']['source_names']) == 1 for unit in units)


def test_hosted_bookmark_only_queues_link_without_website_or_model(setup, monkeypatch):
    from application_service.vercel_workflows import execute_unit
    from application_service import hosted_registry
    from services.workload_registry import WorkloadRegistry
    from services.approval_registry import ApprovalRegistry
    repository, runs, record, engine, published, calls = setup
    repository.save_profile({'schema_version': 1, 'personal': {}, 'professional': {}, 'employment': []})
    def forbidden(*args, **kwargs): pytest.fail('Website extraction belongs to ChatGPT validation')
    monkeypatch.setattr('application_service.vercel_workflows.persistence_client', lambda: RuntimeClient(repository))
    monkeypatch.setattr('application_service.vercel_workflows.provider', forbidden)
    monkeypatch.setattr(hosted_registry, 'HostedWorkloads', WorkloadRegistry)
    monkeypatch.setattr(hosted_registry, 'HostedApprovals', ApprovalRegistry)
    monkeypatch.setattr('services.bookmark_read.read_discovered_posting', forbidden)
    identity = record['application_ids'][0]
    result = execute_unit(record, {'kind': 'queue', 'unit_id': 'one', 'application_id': identity})
    assert result['status'] == 'succeeded'
    assert repository.get_application(identity).status == 'Bookmarked'
    assert repository.list_artifacts(identity) == []
