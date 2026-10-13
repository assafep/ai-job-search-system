from services import workload_runner


def test_execute_requires_persistence_client_and_executes_operations():
    calls = []

    class FakeClient:
        def write_research(self, *args):
            calls.append(("research", args))
            return {"research_tab_id": "r-1"}

        def append_event(self, *args):
            calls.append(("event", args))
            return {"event_id": "e-1"}

        def transition(self, *args):
            calls.append(("transition", args))
            return {"status": "Interviewing"}

    result = workload_runner.execute(
        "prepare-interview",
        {"workload_id": "work-live", "application_id": "app-1"},
        {"interview-prep": {"briefing": "Discuss reliability."}},
        FakeClient(),
    )
    assert result["execution"]["dry_run"] is False
    assert [kind for kind, args in calls] == ["transition", "research", "event"]
