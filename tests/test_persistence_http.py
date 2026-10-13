import io
import json
import urllib.error

from persistence_api import main
from persistence_api.repository import MemoryRepository


def test_profile_put_and_get_use_persistence_api(monkeypatch):
    monkeypatch.setattr(main, "repository", MemoryRepository())
    profile = {
        "schema_version": 1,
        "personal": {"full_name": "Example"},
        "professional": {},
        "employment": [],
    }

    handler = object.__new__(main.Handler)
    handler.path = "/profile"
    handler.headers = {"Content-Length": str(len(json.dumps(profile).encode("utf-8")))}
    handler.rfile = io.BytesIO(json.dumps(profile).encode("utf-8"))
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))
    handler.do_PUT()
    assert responses[-1] == (200, profile)

    handler.do_GET()
    assert responses[-1] == (200, profile)


def test_artifact_endpoint_accepts_base64_content(monkeypatch):
    repository = MemoryRepository()
    monkeypatch.setattr(main, "repository", repository)
    application = repository.create_application("Example", "Backend", "https://example.test/job")
    payload = json.dumps({
        "name": "resume.pdf",
        "mime_type": "application/pdf",
        "content_base64": "cGRm",
    }).encode("utf-8")

    handler = object.__new__(main.Handler)
    handler.path = "/applications/{}/artifacts".format(application.application_id)
    handler.headers = {"Content-Length": str(len(payload))}
    handler.rfile = io.BytesIO(payload)
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))
    handler.do_POST()

    assert responses[-1][0] == 201
    stored_id = responses[-1][1].artifact_id
    assert repository.artifact_contents[stored_id] == b"pdf"

    handler.path = "/applications/{}/artifacts".format(application.application_id)
    handler.do_GET()
    assert responses[-1][0] == 200
    assert responses[-1][1][0].artifact_id == stored_id

    handler.path = "/applications/{}/artifacts/{}".format(application.application_id, stored_id)
    handler.do_GET()
    assert responses[-1][0] == 200
    assert responses[-1][1]["content_base64"] == "cGRm"


def test_research_endpoint_reads_nested_research(monkeypatch):
    repository = MemoryRepository()
    monkeypatch.setattr(main, "repository", repository)
    application = repository.create_application("Example", "Backend", "https://example.test/job")
    repository.write_research(application.application_id, "Research", "Ask about scale")

    handler = object.__new__(main.Handler)
    handler.path = "/applications/{}/research".format(application.application_id)
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))
    handler.do_GET()

    assert responses[-1][0] == 200
    assert responses[-1][1][0]["title"] == "Research"
    assert responses[-1][1][0]["content"] == "Ask about scale"


def test_root_relative_file_endpoint_round_trip(monkeypatch):
    repository = MemoryRepository()
    monkeypatch.setattr(main, "repository", repository)
    payload = json.dumps({
        "path": "shared/notes.md",
        "mime_type": "text/markdown",
        "content_base64": "bm90ZXM=",
        "idempotency_key": "file-1",
    }).encode("utf-8")
    handler = object.__new__(main.Handler)
    handler.path = "/files"
    handler.headers = {"Content-Length": str(len(payload))}
    handler.rfile = io.BytesIO(payload)
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))
    handler.do_POST()
    assert responses[-1][0] == 201

    handler.path = "/files?path=shared%2Fnotes.md"
    handler.do_GET()
    assert responses[-1] == (200, {
        "file": responses[-1][1]["file"],
        "content_base64": "bm90ZXM=",
    })


def test_http_serializer_recursively_serializes_artifact_response():
    repository = MemoryRepository()
    application = repository.create_application("Example", "Backend", "https://example.test/job")
    artifact = repository.store_artifact(application.application_id, "resume.pdf", b"pdf")

    serialized = main.output({
        "artifact": artifact,
        "content_base64": "cGRm",
    })

    assert serialized["artifact"]["artifact_id"] == artifact.artifact_id
    assert serialized["content_base64"] == "cGRm"


def test_missing_get_resource_returns_not_found(monkeypatch):
    monkeypatch.setattr(main, "repository", MemoryRepository())
    handler = object.__new__(main.Handler)
    handler.path = "/applications/missing/events"
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))

    handler.do_GET()

    assert responses[-1][0] == 404


def test_runtime_configuration_selects_google_unless_memory_is_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOSITORY_BACKEND", raising=False)
    runtime = tmp_path / "runtime.json"
    runtime.write_text("{}")

    assert main.should_use_google_backend(None, str(runtime)) is True
    assert main.should_use_google_backend("", str(runtime)) is True
    assert main.should_use_google_backend("memory", str(runtime)) is False
    assert main.should_use_google_backend("google", str(tmp_path / "missing.json")) is True


def test_missing_write_field_is_bad_request_not_not_found(monkeypatch):
    monkeypatch.setattr(main, "repository", MemoryRepository())
    payload = json.dumps({"company": "Example"}).encode("utf-8")
    handler = object.__new__(main.Handler)
    handler.path = "/applications"
    handler.headers = {"Content-Length": str(len(payload))}
    handler.rfile = io.BytesIO(payload)
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))

    handler.do_POST()

    assert responses[-1] == (400, {"error": "missing required field: role"})


def test_optional_api_token_protects_state_endpoints(monkeypatch):
    monkeypatch.setenv("PERSISTENCE_API_TOKEN", "secret")
    handler = object.__new__(main.Handler)
    handler.path = "/applications"
    handler.headers = {}
    responses = []
    handler.send_response = lambda status: responses.append(("status", status))
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler.wfile = io.BytesIO()

    handler.do_GET()

    assert ("status", 401) in responses


def test_unavailable_google_backend_does_not_fall_back_to_memory(monkeypatch):
    monkeypatch.setattr(main, "repository", None)
    monkeypatch.setattr(main, "repository_startup_error", "missing Google credentials")
    monkeypatch.delenv("PERSISTENCE_API_TOKEN", raising=False)
    handler = object.__new__(main.Handler)
    handler.path = "/applications"
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))

    handler.do_GET()

    assert responses[-1] == (503, {
        "error": "persistence backend unavailable",
        "detail": "missing Google credentials",
        "onboarding_command": "python3 -m services.onboarding",
    })


def test_readiness_distinguishes_liveness_from_backend_availability(monkeypatch):
    monkeypatch.setattr(main, "repository", None)
    monkeypatch.setattr(main, "repository_startup_error", "missing Google credentials")
    handler = object.__new__(main.Handler)
    handler.path = "/ready"
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))

    handler.do_GET()

    assert responses[-1][0] == 503

    monkeypatch.setattr(main, "repository", MemoryRepository())
    handler.do_GET()
    assert responses[-1] == (200, {"ok": True})


def test_application_status_filter_rejects_unknown_state(monkeypatch):
    monkeypatch.setattr(main, "repository", MemoryRepository())
    handler = object.__new__(main.Handler)
    handler.path = "/applications?status=Unknown"
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))

    handler.do_GET()

    assert responses[-1][0] == 400
    assert "status must be one of" in responses[-1][1]["error"]


def test_upstream_drive_failure_is_retryable_service_error(monkeypatch):
    class UnavailableRepository:
        def list_applications(self, status=None):
            raise urllib.error.URLError("Drive unavailable")

    monkeypatch.setattr(main, "repository", UnavailableRepository())
    handler = object.__new__(main.Handler)
    handler.path = "/applications"
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))

    handler.do_GET()

    assert responses[-1][0] == 503
    assert "Drive unavailable" in responses[-1][1]["error"]


def test_journal_status_and_explicit_repair_endpoints(monkeypatch):
    monkeypatch.setattr(main, "repository", MemoryRepository())
    handler = object.__new__(main.Handler)
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))

    handler.path = "/journal/status"
    handler.do_GET()
    assert responses[-1][0] == 200
    assert responses[-1][1]["valid"] is True

    handler.path = "/journal/repair"
    handler.headers = {"Content-Length": "2"}
    handler.rfile = io.BytesIO(b"{}")
    handler.do_POST()
    assert responses[-1][0] == 200
    assert responses[-1][1]["missing_states"] == []
    assert responses[-1][1]["duplicate_states"] == []
