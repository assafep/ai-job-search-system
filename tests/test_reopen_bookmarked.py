import json
from pathlib import Path

from application_service.repository import MemoryRepository
from services.orchestrator import PREFIX, Orchestrator, digest, version
from services.workload_registry import WorkloadRegistry


def test_dead_letter_does_not_keep_a_bookmarked_posting_out_of_validation(tmp_path):
    repository = MemoryRepository()
    application = repository.create_application(
        "Example", "Backend", "https://jobs.example/backend", target_state="Bookmarked")
    config = json.loads(Path("config/pipelines.json").read_text())
    config["poll_seconds"] = 1
    config["scan_seconds"] = 1
    config["discovery_workers"] = 0
    config.pop("discovery_seconds", None)
    for name, spec in config["pipelines"].items():
        spec["workers"] = 1 if name == "validate" else 0
    path = tmp_path / "pipelines.json"
    path.write_text(json.dumps(config))
    registry = WorkloadRegistry()
    app = {"application_id": application.application_id, "status": application.status,
           "company": application.company, "role": application.role, "posting_url": application.posting_url,
           "last_event_id": getattr(application, "last_event_id", None)}
    workload_id = PREFIX + digest(["validate", "validate", application.application_id, version(app)])
    record = registry.enqueue({
        "workload_id": workload_id,
        "workflow": PREFIX + "validate",
        "event": {"event_id": workload_id, "event_type": "pipeline_item", "payload": {
            "pipeline": "validate",
            "application_id": application.application_id,
            "spec": config["pipelines"]["validate"],
        }},
    })
    for _ in range(3):
        registry.claim(record.workload_id)
        registry.fail(record.workload_id, "completed journal is full")
    assert registry.get(record.workload_id).status == "dead_letter"

    def agents(spec, app, workload_id, event=None):
        return {"outcome": "validated", "output": {"is_match": True, "reason": "fit"}}

    orchestrator = Orchestrator(repository, registry, agents, str(path))
    try:
        orchestrator.last_scan = float("-inf")
        orchestrator.tick()
        for future in list(orchestrator.inflight):
            future.result(timeout=10)
        assert repository.get_application(application.application_id).status == "Validated"
    finally:
        orchestrator.close()
