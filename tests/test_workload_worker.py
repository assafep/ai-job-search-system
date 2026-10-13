from services.event_dispatcher import dispatch
from services.workload_registry import WorkloadRegistry
from services.workflow_runtime import WorkflowRuntime
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
        WorkflowRuntime(registry, Client()),
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
        WorkflowRuntime(registry, object()),
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
