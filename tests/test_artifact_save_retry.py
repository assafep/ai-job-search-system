import base64
import json
from pathlib import Path

from application_service.repository import MemoryRepository
from services.orchestrator import Orchestrator


def _config(tmp_path):
    config = json.loads(Path("config/pipelines.json").read_text())
    config["poll_seconds"] = 1
    config["scan_seconds"] = 1
    config["discovery_workers"] = 0
    config.pop("discovery_seconds", None)
    path = tmp_path / "pipelines.json"
    path.write_text(json.dumps(config))
    return path


def test_dropped_artifact_connection_retries_without_dead_lettering(tmp_path):
    repository = MemoryRepository()
    application = repository.create_application(
        "ScyllaDB", "Cloud Software Engineer", "https://www.comeet.com/jobs/scylladb/example",
        target_state="Discovered")
    saves = {"count": 0}
    original = repository.store_artifact

    def flaky(application_id, name, content, mime_type=None, idempotency_key=None):
        saves["count"] += 1
        if saves["count"] == 1:
            raise ConnectionError("Remote end closed connection without response")
        return original(application_id, name, content, mime_type, idempotency_key)

    repository.store_artifact = flaky
    calls = []

    def agents(spec, app, workload_id, event=None):
        calls.append(workload_id)
        body = json.dumps({"description": "Backend platform role. " * 40}).encode()
        return {
            "outcome": "read",
            "output": {
                "company": "ScyllaDB",
                "role": "Cloud Software Engineer",
                "posting_url": application.posting_url,
                "description": "Backend platform role. " * 40,
                "read_failure": "",
            },
            "artifacts": [{
                "name": "posting.json",
                "content_base64": base64.b64encode(body).decode("ascii"),
                "mime_type": "application/json",
                "label": "Posting content",
            }],
        }

    from services.workload_registry import WorkloadRegistry
    registry = WorkloadRegistry()
    orchestrator = Orchestrator(repository, registry, agents, config_path=str(_config(tmp_path)))
    spec = {
        "states": ["Discovered"],
        "agent": "read-posting",
        "workers": 1,
        "routes": {
            "read": "Bookmarked",
            "read_failure": "Manual Apply Required",
            "system_error": "Dead Letters Queue",
        },
    }
    registry.enqueue({
        "workload_id": "pipeline-bookmark-1",
        "workflow": "pipeline-bookmark",
        "event": {
            "event_id": "pipeline-bookmark-1",
            "event_type": "pipeline_item",
            "payload": {
                "pipeline": "bookmark",
                "spec": spec,
                "application_id": application.application_id,
            },
        },
    })
    try:
        try:
            orchestrator._process("pipeline-bookmark-1")
        except ConnectionError as error:
            assert "Remote end closed connection" in str(error)
        else:
            raise AssertionError("expected the dropped connection to be retried")
        assert repository.get_application(application.application_id).status == "Discovered"
        assert registry.get("pipeline-bookmark-1").status == "failed"
        assert calls == ["pipeline-bookmark-1"]

        orchestrator._process("pipeline-bookmark-1")
        assert repository.get_application(application.application_id).status == "Bookmarked"
        assert calls == ["pipeline-bookmark-1"]
        assert saves["count"] == 2
    finally:
        orchestrator.pool.shutdown(wait=False)
