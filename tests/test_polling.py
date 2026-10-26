from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from application_service.repository import MemoryRepository
from services.polling import PollingSchedule, definitions, enqueue_scheduled, enqueue_queues, maintenance_names
from services.workload_registry import WorkloadRegistry


def test_all_workflows_declare_polling_and_supported_cadence():
    workflows = definitions()
    assert len(workflows) == 11
    assert all(d['trigger']['type'] == 'polling' for d in workflows.values())
    assert workflows['prepare-interview']['trigger']['cadence'] == 'day'
    assert all(d['trigger']['cadence'] == 'minute' for name, d in workflows.items() if name != 'prepare-interview')
    assert workflows['complete-journal']['input_states'] == ['Rejected', 'Invalidated', 'Duplicate']


def test_shared_clock_limits_failed_polls_without_delaying_other_polls():
    now = [0]
    scheduler = PollingSchedule(clock=lambda: now[0])
    calls = []
    scheduler.run('mail', 60, lambda: calls.append('mail'))
    now[0] = 59
    scheduler.run('mail', 60, lambda: calls.append('early'))
    scheduler.run('queues', 5, lambda: calls.append('queues'))
    now[0] = 60
    with pytest.raises(RuntimeError):
        scheduler.run('mail', 60, lambda: (_ for _ in ()).throw(RuntimeError('offline')))
    scheduler.run('mail', 60, lambda: calls.append('too-soon'))
    now[0] = 120
    scheduler.run('mail', 60, lambda: calls.append('retry'))
    assert calls == ['mail', 'queues', 'retry']


def test_minute_admission_shared_across_restart_and_does_not_backlog_busy_work(tmp_path):
    registry_path = str(tmp_path / 'workloads.json')
    registry = WorkloadRegistry(storage_path=registry_path)
    now = datetime(2026, 10, 25, 23, 59, tzinfo=timezone.utc)
    first = enqueue_scheduled(registry, now=now)
    restarted = WorkloadRegistry(storage_path=registry_path)
    second = enqueue_scheduled(restarted, now=now + timedelta(seconds=30))
    assert {e['workload_id'] for e in first} == {e['workload_id'] for e in second}
    assert {e['workflow'] for e in first} == {'discover', *maintenance_names()}
    assert {e['workload_id'] for e in enqueue_scheduled(restarted, now=now + timedelta(minutes=1))} == {e['workload_id'] for e in first}
    for record in restarted.pending():
        restarted.claim(record.workload_id)
        restarted.complete(record.workload_id)
    third = enqueue_scheduled(restarted, now=now + timedelta(minutes=1))
    assert not {e['workload_id'] for e in first} & {e['workload_id'] for e in third}


def test_queue_polling_reads_each_declared_state_and_never_admits_manual_or_dead_letters():
    class Client:
        def list_applications(self, status):
            return [{'application_id': status, 'status': status}]
    registry = WorkloadRegistry()
    first = enqueue_queues(Client(), registry)
    assert {e['event']['payload']['application_id'] for e in first} == {
        'Discovered', 'Bookmarked', 'Validated', 'Prepared'}
    assert enqueue_queues(Client(), registry) == []
    for record in registry.pending():
        registry.claim(record.workload_id)
        registry.complete(record.workload_id)
    enqueue_queues(Client(), registry)
    assert len(registry.snapshot()) == 4


def test_orchestrator_runs_all_maintenance_without_recovering_active_workers(monkeypatch, tmp_path):
    from services.orchestrator import Orchestrator
    repository = MemoryRepository()
    repository.prepare_gmail_grant = lambda: None
    repository.complete_terminal_journal = lambda: []
    repository.bulk_update_items = lambda items, **kwargs: {'failed': 0, 'results': []}
    config = json.loads(Path('config/pipelines.json').read_text())
    config.pop('discovery_seconds')
    for spec in config['pipelines'].values():
        spec['workers'] = 0
    config_path = tmp_path / 'pipelines.json'
    config_path.write_text(json.dumps(config))
    registry = WorkloadRegistry()
    calls = []
    def run(self, workload_id, **kwargs):
        assert kwargs['recover_incomplete'] is False
        record = registry.claim(workload_id)
        calls.append(record.workflow)
        registry.complete(workload_id)
    monkeypatch.setattr('services.workflow_runtime.WorkflowRuntime.run', run)
    orchestrator = Orchestrator(repository, registry, None, str(config_path))
    try:
        orchestrator.tick()
        for future in list(orchestrator.inflight):
            future.result(timeout=5)
        orchestrator.tick()
        assert set(calls) == set(maintenance_names())
        assert len(calls) == len(set(calls))
    finally:
        orchestrator.close()


def test_runtime_reconciliation_does_not_reset_live_work_when_recovery_disabled():
    from services.event_dispatcher import dispatch
    from services.workflow_runtime import WorkflowRuntime
    registry = WorkloadRegistry()
    active = dispatch('validate_schedule', {'application_id': 'live'}, 'live', registry)
    registry.claim(active['workload_id'])
    reconciliation = dispatch('reconciliation_schedule', {}, 'daily', registry)
    WorkflowRuntime(registry, MemoryRepository()).run(
        reconciliation['workload_id'], agent_results={}, recover_incomplete=False)
    assert registry.get(active['workload_id']).status == 'running'


def test_terminal_journal_api_owns_cleanup_and_is_safe_to_repeat(monkeypatch):
    from http.server import HTTPServer
    from threading import Thread
    from application_service import main
    from services.application_service_client import ApplicationServiceClient
    from services.completion_workflow import execute

    class Journal:
        def __init__(self):
            self.apps = [
                {'application_id': 'r', 'status': 'Rejected'},
                {'application_id': 'i', 'status': 'Invalidated'},
                {'application_id': 'd', 'status': 'Duplicate'},
                {'application_id': 'a', 'status': 'Applied'}]
            self.completed = []
            self.removed = []
        def get_application(self, identity):
            return next(app for app in self.apps if app['application_id'] == identity)
        def store_file(self, path, content, *args):
            if not hasattr(self, 'files'): self.files = {}
            self.files[path] = {'content': content}
        def read_file(self, path):
            return getattr(self, 'files', {})[path]
        def list_main_journal_applications(self, statuses):
            return [app for app in self.apps if app['status'] in statuses]
        def complete_application(self, identity, reason):
            self.completed.append((identity, reason))
            self.apps = [app for app in self.apps if app['application_id'] != identity]
        def remove_tab(self, identity):
            self.removed.append(identity)
            self.apps = [app for app in self.apps if app['application_id'] != identity]

    journal = Journal()
    monkeypatch.setattr(main, 'repository', journal)
    monkeypatch.setenv('APPLICATION_SERVICE_TOKEN', 'test-token')
    server = HTTPServer(('127.0.0.1', 0), main.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = ApplicationServiceClient('http://127.0.0.1:{}'.format(server.server_port), auth_token='test-token')
    try:
        results = execute(client)
        assert [r['action'] for r in results] == ['completed', 'completed', 'removed']
        assert journal.completed == [('r', 'Rejected'), ('i', 'Invalidated')]
        assert journal.removed == ['d']
        assert execute(client) == []
        assert journal.apps == [{'application_id': 'a', 'status': 'Applied'}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_daemon_leaves_native_browser_work_pending_without_busy_loop(monkeypatch):
    from services.automation_daemon import AutomationDaemon
    registry = WorkloadRegistry()
    app = {'application_id': 'one', 'status': 'Discovered'}
    calls = []
    class Client:
        def list_applications(self, status):
            return [dict(app)] if app['status'] == status else []
    class Runtime:
        def __init__(self):
            self.registry = registry
        def run(self, workload_id, agent_provider=None):
            record = registry.claim(workload_id)
            calls.append(record.workflow)
            target = {'bookmark-discovered': 'Bookmarked', 'validate-bookmarked': 'Validated',
                      'prepare-validated': 'Prepared', 'submit-prepared': 'Applied'}.get(record.workflow)
            if target:
                app['status'] = target
            registry.complete(workload_id)
            return {'workflow': record.workflow, 'status': 'succeeded'}
    monkeypatch.setattr('services.external_event_polling.poll', lambda *args, **kwargs: {})
    daemon = AutomationDaemon(registry, Runtime(), Client(), lambda *args: {})
    daemon.run_cycle()
    assert app['status'] == 'Bookmarked'
    assert set(calls) == set(maintenance_names()) | {'discover', 'bookmark-discovered'}
    assert {record.workflow for record in registry.pending()} == {'validate-bookmarked'}
    prior = list(calls)
    daemon.run_cycle()
    assert calls == prior


def test_reference_backend_terminal_cleanup_preserves_active_applications():
    from application_service.terminal_journal import complete_terminal
    repository = MemoryRepository()
    for state in ('Rejected', 'Invalidated', 'Duplicate', 'Applied'):
        app = repository.create_application(state, 'Role', 'https://example.test/' + state)
        app.status = state
    assert len(complete_terminal(repository)) == 3
    assert complete_terminal(repository) == []
    assert len(repository.list_applications('Applied')) == 1
    assert repository.list_applications('Duplicate') == []
