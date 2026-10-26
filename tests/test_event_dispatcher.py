import json

from services.event_dispatcher import dispatch, dispatch_normalized
from services.workload_registry import WorkloadRegistry
from services.event_dispatcher import main


def test_calendar_event_routes_to_interview_preparation():
    workload = dispatch("calendar_event", {"event_id": "calendar-1"}, "event-1")
    assert workload["stage"] == "prepare-interview"
    assert workload["event"]["event_id"] == "event-1"
    assert workload["status"] == "queued"


def test_unknown_event_is_rejected():
    try:
        dispatch("unknown", {})
    except ValueError:
        return
    raise AssertionError("unknown events must not be routed")


def test_repeated_event_has_same_workload_identity():
    first = dispatch("calendar_event", {"meeting": "m-1"}, event_id="event-1")
    retry = dispatch("calendar_event", {"meeting": "m-1"}, event_id="event-1")
    different = dispatch("calendar_event", {"meeting": "m-2"}, event_id="event-2")

    assert first["workload_id"] == retry["workload_id"]
    assert first["workload_id"] != different["workload_id"]


def test_dispatch_can_enqueue_into_registry():
    registry = WorkloadRegistry()
    envelope = dispatch("reconciliation_schedule", {}, "event-reconcile", registry)
    assert registry.records[envelope["workload_id"]].stage == "reconcile"


def test_normalized_adapter_event_routes_through_same_dispatcher():
    envelope = dispatch_normalized({
        "event_type": "incoming_email",
        "event_id": "mail-1",
        "payload": {"subject": "Next steps"},
    })
    assert envelope["stage"] == "handle-incoming-email"
    assert envelope["event"]["event_id"] == "mail-1"


def test_normalized_event_rejects_invalid_identity_and_payload():
    for event in (
        {"event_type": "calendar_event", "event_id": "", "payload": {}},
        {"event_type": "calendar_event", "event_id": "event-1", "payload": []},
    ):
        try:
            dispatch_normalized(event)
        except ValueError:
            continue
        raise AssertionError("malformed normalized event should be rejected")


def test_dispatch_cli_can_durably_enqueue_normalized_event(tmp_path, monkeypatch, capsys):
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({
        "event_type": "calendar_event",
        "event_id": "calendar-cli-1",
        "payload": {"application_id": "app-1"},
    }))
    registry_path = tmp_path / "workloads.json"
    monkeypatch.setenv("WORKLOAD_REGISTRY_PATH", str(registry_path))

    assert main(["--event-file", str(event_file), "--enqueue"]) == 0

    envelope = json.loads(capsys.readouterr().out)
    persisted = json.loads(registry_path.read_text())
    assert envelope["stage"] == "prepare-interview"
    assert envelope["workload_id"] in persisted
