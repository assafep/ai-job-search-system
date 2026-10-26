from services.event_dispatcher import dispatch
from services.workload_registry import WorkloadRegistry
from services.stage_executor import StageExecutor
from services.workload_worker import WorkloadWorker


def test_worker_drains_pending_workload_with_agent_provider():
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch(
        "calendar_event", {"application_id": "app-1"}, "worker-calendar-1"
    ))

    class Client:
        def get_profile(self):
            return {}

        def get_application(self, application_id):
            return {"application_id": application_id, "status": "Applied"}

        def list_events(self, application_id):
            return []

        def list_research(self, application_id):
            return []

        def transition(self, *args):
            return {"status": "Interviewing"}

        def write_research(self, *args):
            return {"research_tab_id": "research-1"}

        def append_event(self, *args):
            return {"event_id": "event-1"}

    worker = WorkloadWorker(
        StageExecutor(registry, Client()),
        lambda prompt, context: {"briefing": "Discuss reliability."},
    )
    results = worker.run_once()

    assert results[0]["status"] == "succeeded"
    assert registry.get(record.workload_id).status == "succeeded"


def test_worker_reports_provider_failure_and_keeps_registry_failure_state():
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch(
        "calendar_event", {"application_id": "app-1"}, "worker-calendar-failure-1"
    ))
    worker = WorkloadWorker(
        StageExecutor(registry, object()),
        lambda prompt, context: {"invalid": "result"},
    )

    results = worker.run_once()

    assert results[0]["status"] == "failed"
    assert registry.get(record.workload_id).status == "failed"


def test_worker_rejects_invalid_limits_and_poll_intervals():
    worker = WorkloadWorker(object(), lambda prompt, context: {})
    for value in (0, -1, True, 1.5):
        try:
            worker.run_once(value)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid limit was accepted")
    for value in (-1, True, "30"):
        try:
            worker.run_forever(value)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid poll interval was accepted")


def test_shutdown_finishes_current_item_and_leaves_next_unclaimed():
    from types import SimpleNamespace
    from threading import Event
    stop = Event()
    seen = []
    records = [SimpleNamespace(workload_id='first', stage='discover'),
               SimpleNamespace(workload_id='next', stage='discover')]
    class Runtime:
        registry = SimpleNamespace(pending=lambda: records)
        def run(self, workload_id, **kwargs):
            seen.append(workload_id)
            stop.set()
            return 'completed'
    worker = WorkloadWorker(Runtime(), lambda *_: {})
    assert worker.run_once(stop_event=stop) == ['completed']
    assert seen == ['first']


def test_cli_worker_leaves_native_browser_stages_unclaimed():
    from types import SimpleNamespace
    records = [SimpleNamespace(workload_id=name, stage=name)
               for name in ('validate-bookmarked', 'submit-prepared', 'discover')]
    seen = []
    runtime = SimpleNamespace(registry=SimpleNamespace(pending=lambda: records),
                              run=lambda workload_id, **kwargs: seen.append(workload_id))
    WorkloadWorker(runtime, lambda *_: {}).run_once()
    assert seen == ['discover']


def test_automatic_polling_respects_stage_host_and_cadence(monkeypatch):
    from threading import Event
    from types import SimpleNamespace
    from services import polling
    stop = Event()
    admitted = []
    specs = {'discover': 'scheduled', 'prepare-validated': 'queue',
             'validate-bookmarked': 'queue', 'submit-prepared': 'queue', 'prepare-interview': 'external'}
    monkeypatch.setattr(polling, 'definitions', lambda: {name: {'trigger': {'poll_kind': kind}} for name, kind in specs.items()})
    monkeypatch.setattr(polling, 'stage_poll_seconds', lambda name: 60)
    monkeypatch.setattr(polling, 'enqueue_scheduled', lambda registry, names: admitted.extend(names))
    monkeypatch.setattr(polling, 'enqueue_queues', lambda client, registry, names: admitted.extend(names))
    runtime = SimpleNamespace(persistence_client=object(), registry=object())
    worker = WorkloadWorker(runtime, lambda *_: {})
    ticks = []
    def run_once(**kwargs):
        ticks.append(1)
        if len(ticks) == 2:
            stop.set()
    monkeypatch.setattr(worker, 'run_once', run_once)
    worker.run_forever(0, stop_event=stop, poll_stages=True)
    assert admitted == ['discover', 'prepare-validated']
