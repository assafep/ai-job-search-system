import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application_service.repository import MemoryRepository
from services.orchestrator import Orchestrator, provider_quota_until
from services.workload_registry import WorkloadRegistry


QUOTA_ERROR = (
    "Codex CLI failed (1): resume text that is not the failure. "
    "ERROR: You've hit your usage limit. Upgrade to Pro. "
    "try again at Oct 19th, 2026 12:56 AM."
)


def test_provider_quota_until_parses_the_reset_time():
    now = datetime(2026, 10, 18, 19, 0, tzinfo=timezone.utc)
    until = provider_quota_until(QUOTA_ERROR, now)
    assert until is not None
    assert until > now
    assert until - now < timedelta(hours=24)


def test_provider_quota_until_ignores_other_failures():
    assert provider_quota_until("saved posting content is missing") is None


def test_registry_defer_does_not_consume_an_attempt(tmp_path):
    registry = WorkloadRegistry(storage_path=str(tmp_path / "workloads.json"))
    record = registry.enqueue({
        "workload_id": "pipeline-1",
        "stage": "pipeline-prepare",
        "event": {"event_id": "pipeline-1", "event_type": "pipeline_item", "payload": {}},
    })
    registry.claim(record.workload_id)
    deferred = registry.defer(record.workload_id, "provider quota exhausted")
    assert deferred.status == "queued"
    assert deferred.attempts == 0


def _prepare_config(tmp_path):
    config = json.loads(Path("config/pipelines.json").read_text())
    config["poll_seconds"] = 1
    config["scan_seconds"] = 1
    config["discovery_workers"] = 0
    config.pop("discovery_seconds", None)
    path = tmp_path / "pipelines.json"
    path.write_text(json.dumps(config))
    return path


def _prepare_workload(repository, registry, application_id):
    spec = {
        "states": ["Validated"],
        "agent": "application",
        "workers": 1,
        "routes": {
            "prepared": "Prepared",
            "failed": "Manual Apply Required",
            "duplicate": "Duplicate",
            "system_error": "Dead Letters Queue",
        },
    }
    record = registry.enqueue({
        "workload_id": "pipeline-prepare-1",
        "stage": "pipeline-prepare",
        "event": {
            "event_id": "pipeline-prepare-1",
            "event_type": "pipeline_item",
            "payload": {
                "pipeline": "prepare",
                "spec": spec,
                "application_id": application_id,
            },
        },
    })
    return record


def test_usage_limit_leaves_the_validated_posting_in_place(tmp_path):
    repository = MemoryRepository()
    application = repository.create_application(
        "PointFive", "Staff Backend Engineer", "https://jobs.example/pointfive", target_state="Validated")
    registry = WorkloadRegistry()
    calls = []

    def agents(spec, app, workload_id, event=None):
        calls.append(workload_id)
        raise RuntimeError(QUOTA_ERROR)

    orchestrator = Orchestrator(repository, registry, agents, config_path=str(_prepare_config(tmp_path)))
    try:
        _prepare_workload(repository, registry, application.application_id)
        orchestrator._process("pipeline-prepare-1")
        assert repository.get_application(application.application_id).status == "Validated"
        record = registry.get("pipeline-prepare-1")
        assert record.status == "queued"
        assert record.attempts == 0
        assert orchestrator.provider_backoff_until is not None
        assert "provider quota exhausted" in orchestrator.last_error

        orchestrator.last_scan = float("-inf")
        orchestrator.tick()
        assert calls == ["pipeline-prepare-1"]
        assert repository.get_application(application.application_id).status == "Validated"
    finally:
        orchestrator.pool.shutdown(wait=False)


def test_other_prepare_errors_still_reach_the_dead_letter_queue(tmp_path):
    repository = MemoryRepository()
    application = repository.create_application(
        "PointFive", "Staff Backend Engineer", "https://jobs.example/pointfive", target_state="Validated")
    registry = WorkloadRegistry()

    def agents(spec, app, workload_id, event=None):
        raise RuntimeError("saved posting content belongs to another position")

    orchestrator = Orchestrator(repository, registry, agents, config_path=str(_prepare_config(tmp_path)))
    try:
        _prepare_workload(repository, registry, application.application_id)
        orchestrator._process("pipeline-prepare-1")
        assert repository.get_application(application.application_id).status == "Dead Letters Queue"
    finally:
        orchestrator.pool.shutdown(wait=False)
