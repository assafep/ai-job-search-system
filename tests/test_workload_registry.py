import json

import pytest
from http.server import HTTPServer
from threading import Thread

from services.event_dispatcher import dispatch
from services.application_service_client import ApplicationServiceClient
from services.runtime_config import OnboardingRequired
from services.workload_registry import WorkloadRegistry, registry_from_environment
from services.workflow_runtime import WorkflowRuntime, runtime_from_environment
from services.approval_registry import ApprovalRegistry
from application_service import main as persistence_main
from application_service.repository import MemoryRepository


def test_registry_deduplicates_and_recovers_workload():
    registry = WorkloadRegistry(max_attempts=2)
    envelope = dispatch("incoming_email", {"message_id": "m-1"}, "event-1")
    first = registry.enqueue(envelope)
    retry = registry.enqueue(envelope)
    assert first is retry

    registry.claim(first.workload_id)
    recovered = registry.recover_incomplete()
    assert recovered[0].status == "queued"
    assert registry.claim(first.workload_id).attempts == 2
    registry.fail(first.workload_id, "temporary error")
    assert registry.records[first.workload_id].status == "dead_letter"


def test_registry_loads_records_saved_before_result_checkpoints(tmp_path):
    path = tmp_path / "workloads.json"
    path.write_text(json.dumps({"work_1": {
        "workload_id": "work_1",
        "workflow": "discover",
        "event": {"event_id": "event-1", "event_type": "pipeline_item", "payload": {}},
        "status": "succeeded",
        "attempts": 1,
        "error": None,
        "created_at": "2026-10-13T00:00:00+00:00",
        "updated_at": "2026-10-13T00:00:00+00:00",
    }}))
    registry = WorkloadRegistry(storage_path=str(path))
    assert registry.get("work_1").result is None
    registry.checkpoint_result("work_1", {"value": {"outcome": "accepted"}})
    reloaded = WorkloadRegistry(storage_path=str(path))
    assert reloaded.get("work_1").result["value"]["outcome"] == "accepted"


def test_registry_completes_only_claimed_workloads():
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch("calendar_event", {}, "event-2"))
    try:
        registry.complete(record.workload_id)
    except ValueError:
        pass
    else:
        raise AssertionError("queued workload should not complete")
    registry.claim(record.workload_id)
    assert registry.complete(record.workload_id).status == "succeeded"


def test_registry_report_surfaces_attention_records():
    registry = WorkloadRegistry(max_attempts=1)
    record = registry.enqueue(dispatch("incoming_email", {}, "email-report"))
    registry.claim(record.workload_id)
    registry.fail(record.workload_id, "mail adapter unavailable")

    report = registry.report()

    assert report["counts"]["dead_letter"] == 1
    assert report["attention"][0]["workload_id"] == record.workload_id
    assert report["attention"][0]["error"] == "mail adapter unavailable"


def test_reconciliation_runtime_recovers_interrupted_workloads_before_claiming():
    registry = WorkloadRegistry()
    interrupted = registry.enqueue(dispatch("incoming_email", {}, "email-interrupted"))
    registry.claim(interrupted.workload_id)
    reconcile = registry.enqueue(dispatch("reconciliation_schedule", {}, "reconcile-recovery"))

    class FakeClient:
        pass

    result = WorkflowRuntime(registry, FakeClient()).run(reconcile.workload_id, {})

    assert result["status"] == "succeeded"
    assert registry.get(interrupted.workload_id).status == "queued"
    assert result["registry_report"]["attention"][0]["workload_id"] == interrupted.workload_id


def test_reconciliation_runtime_reports_pending_approvals():
    registry = WorkloadRegistry()
    approvals = ApprovalRegistry()
    approvals.request("send_recruitment_email", "work-pending-approval")
    reconcile = registry.enqueue(dispatch("reconciliation_schedule", {}, "reconcile-approval-report"))

    result = WorkflowRuntime(registry, object(), approvals).run(reconcile.workload_id, {})

    assert result["approval_report"]["counts"]["pending"] == 1


def test_runtime_rejects_malformed_approval_actions():
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch(
        "reconciliation_schedule",
        {"approval_actions": ["", 12]},
        "reconcile-invalid-approval-actions",
    ))

    try:
        WorkflowRuntime(registry, object()).run(record.workload_id, {})
    except ValueError as error:
        assert "non-empty strings" in str(error)
    else:
        raise AssertionError("malformed approval actions should be rejected")

    assert registry.get(record.workload_id).status == "failed"


def test_file_backed_registry_recovers_after_process_restart(tmp_path):
    storage_path = str(tmp_path / "workloads.json")
    first_registry = WorkloadRegistry(storage_path=storage_path)
    record = first_registry.enqueue(dispatch("calendar_event", {}, "event-persisted"))
    first_registry.claim(record.workload_id)

    restarted_registry = WorkloadRegistry(storage_path=storage_path)
    recovered = restarted_registry.recover_incomplete()

    assert recovered[0].workload_id == record.workload_id
    assert recovered[0].status == "queued"
    assert restarted_registry.pending()[0].attempts == 1


def test_registry_from_environment_configures_durable_storage(monkeypatch, tmp_path):
    path = str(tmp_path / "registry.json")
    monkeypatch.setenv("WORKLOAD_REGISTRY_PATH", path)
    monkeypatch.setenv("WORKLOAD_MAX_ATTEMPTS", "5")

    registry = registry_from_environment()

    assert registry.storage_path == path
    assert registry.max_attempts == 5


def test_workflow_runtime_claims_executes_and_completes_workload():
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch(
        "calendar_event",
        {"application_id": "app-1"},
        "calendar-runtime-1",
    ))

    class FakeClient:
        def transition(self, *args):
            return {"status": "Interviewing"}

        def write_research(self, *args):
            return {"research_tab_id": "research-1"}

        def append_event(self, *args):
            return {"event_id": "event-1"}

    result = WorkflowRuntime(registry, FakeClient()).run(
        record.workload_id,
        {"interview-prep": {"briefing": "Discuss reliability."}},
    )

    assert result["status"] == "succeeded"
    assert registry.records[record.workload_id].status == "succeeded"


def test_workflow_runtime_can_invoke_validated_stateless_agent_provider():
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch(
        "calendar_event",
        {"application_id": "app-1"},
        "calendar-provider-1",
    ))
    calls = []

    class FakeClient:
        def get_profile(self):
            return {
                "personal": {"name": "Candidate"},
                "application_defaults": {"calendly_url": "https://calendly.com/profile/intro"},
            }

        def get_application(self, application_id):
            return {"application_id": application_id, "status": "Applied"}

        def list_events(self, application_id):
            return [{"event_type": "application_created"}]

        def list_research(self, application_id):
            return []

        def transition(self, *args):
            return {"status": "Interviewing"}

        def write_research(self, *args):
            return {"research_tab_id": "research-1"}

        def append_event(self, *args):
            return {"event_id": "event-1"}

    def provider(prompt, context):
        calls.append((prompt, context))
        return {"briefing": "Discuss reliability."}

    result = WorkflowRuntime(registry, FakeClient()).run(
        record.workload_id,
        agent_provider=provider,
    )

    assert result["status"] == "succeeded"
    assert len(calls) == 1
    assert calls[0][1]["application_id"] == "app-1"
    assert calls[0][1]["applicant_profile"]["personal"]["name"] == "Candidate"
    assert calls[0][1]["calendly_url"] == "https://calendly.com/profile/intro"
    assert calls[0][1]["application"]["status"] == "Applied"
    assert calls[0][1]["application_events"][0]["event_type"] == "application_created"
    assert calls[0][1]["existing_research"] == []




def test_provider_contract_failure_marks_workload_failed():
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch(
        "calendar_event",
        {"application_id": "app-1"},
        "calendar-provider-failure-1",
    ))

    class UnexpectedClient:
        def transition(self, *args):
            raise AssertionError("persistence must not run after provider failure")

    try:
        WorkflowRuntime(registry, UnexpectedClient()).run(
            record.workload_id,
            agent_provider=lambda prompt, context: {"invalid": "result"},
        )
    except ValueError as error:
        assert "briefing" in str(error)
    else:
        raise AssertionError("invalid provider output should fail the workload")

    assert registry.get(record.workload_id).status == "failed"


def test_runtime_persists_policy_approval_without_performing_external_action():
    registry = WorkloadRegistry()
    approvals = ApprovalRegistry()
    record = registry.enqueue(dispatch(
        "incoming_email",
        {"application_id": "app-1", "approval_actions": ["send_recruitment_email"]},
        "email-approval-1",
    ))

    class FakeClient:
        def append_event(self, *args):
            return {"event_id": "event-1"}

        def transition(self, *args):
            return {"status": "Interviewing"}

    result = WorkflowRuntime(registry, FakeClient(), approvals).run(
        record.workload_id,
        {"correspondence": {
            "intent": "schedule_interview",
            "proposed_action": "draft_response",
            "draft": "Please use my scheduling link.",
        }},
    )

    assert result["approval_outcomes"][0]["decision"] == "ask"
    assert approvals.pending()[0].action == "send_recruitment_email"


def test_workflow_runtime_marks_failed_workload_for_reconciliation():
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch(
        "calendar_event",
        {"application_id": "app-1"},
        "calendar-runtime-failure-1",
    ))

    try:
        WorkflowRuntime(registry, object()).run(
            record.workload_id,
            {"interview-prep": {"briefing": 123}},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("invalid agent output should fail the workload")

    assert registry.records[record.workload_id].status == "failed"
    assert "briefing" in registry.records[record.workload_id].error


def test_runtime_factory_uses_environment_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKLOAD_REGISTRY_PATH", str(tmp_path / "registry.json"))
    fake_client = object()

    runtime = runtime_from_environment(fake_client, require_configuration=False)

    assert runtime.persistence_client is fake_client
    assert runtime.registry.storage_path == str(tmp_path / "registry.json")


def test_runtime_factory_requires_onboarding_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "missing-runtime.json"))

    with pytest.raises(OnboardingRequired):
        runtime_from_environment(object())


def test_calendar_event_flows_through_runtime_and_http_persistence(monkeypatch):
    repository = MemoryRepository()
    monkeypatch.setattr(persistence_main, "repository", repository)
    server = HTTPServer(("127.0.0.1", 0), persistence_main.Handler)
    thread = Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    try:
        client = ApplicationServiceClient("http://127.0.0.1:{}".format(server.server_port))
        application = client.create_application(
            "Example", "Backend", "https://example.test/runtime"
        )
        client.transition(application["application_id"], "Prepared")
        application = client.transition(application["application_id"], "Applied")
        registry = WorkloadRegistry()
        record = registry.enqueue(dispatch(
            "calendar_event",
            {"application_id": application["application_id"]},
            "calendar-end-to-end-1",
        ))

        result = WorkflowRuntime(registry, client).run(
            record.workload_id,
            {"interview-prep": {"briefing": "Discuss reliability."}},
        )

        assert result["status"] == "succeeded"
        assert client.get_application(application["application_id"])["status"] == "Interviewing"
        assert client.list_research(application["application_id"])[0]["title"] == "Research — Interview Briefing"
        assert len(client.list_events(application["application_id"])) == 5
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
