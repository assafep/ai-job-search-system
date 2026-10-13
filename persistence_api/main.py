import base64
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
import urllib.error

import os

from .repository import MemoryRepository
from .journal_format import STATE_TABS


def should_use_google_backend(configured_backend=None, runtime_config_path=None):
    configured_backend = (
        os.environ.get("REPOSITORY_BACKEND")
        if configured_backend is None else configured_backend
    )
    # Compose may pass an intentionally empty variable. Treat it like an
    # unset override so a completed runtime.json can select the Drive backend.
    configured_backend = configured_backend.strip().lower() if configured_backend else None
    runtime_config_path = runtime_config_path or os.environ.get(
        "RUNTIME_CONFIG_PATH",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config", "runtime.json")),
    )
    return configured_backend == "google" or (
        configured_backend is None and os.path.exists(runtime_config_path)
    )


repository = MemoryRepository()
use_google_backend = should_use_google_backend()
repository_startup_error = None
if use_google_backend:
    from .google_docs_repository import repository_from_environment
    try:
        repository = repository_from_environment()
    except Exception as exc:
        # Keep the HTTP process observable when onboarding exists but runtime
        # credentials or Drive settings are not available yet. Never fall
        # back to memory here: that could make a live workload appear to have
        # persisted state when it has not.
        repository = None
        repository_startup_error = str(exc)


def body(request):
    length = int(request.headers.get("Content-Length", "0"))
    return json.loads(request.rfile.read(length) or b"{}")


def required(data, field):
    if field not in data:
        raise ValueError("missing required field: " + field)
    return data[field]


def output(value):
    if hasattr(value, "__dict__"):
        return output(value.__dict__)
    if isinstance(value, list):
        return [output(x) for x in value]
    if isinstance(value, dict):
        return {key: output(item) for key, item in value.items()}
    return value


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, value):
        raw = json.dumps(output(value)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        expected = os.environ.get("PERSISTENCE_API_TOKEN")
        if not expected:
            return True
        return self.headers.get("Authorization") == "Bearer " + expected

    def unauthorized(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", "Bearer")
        self.send_header("Content-Type", "application/json")
        raw = b'{"error":"authentication required"}'
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def backend_unavailable(self):
        return self.send_json(503, {
            "error": "persistence backend unavailable",
            "detail": repository_startup_error or "backend failed to initialize",
            "onboarding_command": "python3 -m services.onboarding",
        })

    def send_exception(self, exc):
        # Distinguish caller validation failures from an unavailable upstream
        # Drive/Docs service so workload runtimes can retry the latter.
        if isinstance(exc, urllib.error.HTTPError):
            status = 502
        elif isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
            status = 503
        else:
            status = 400
        return self.send_json(status, {"error": str(exc)})

    def do_GET(self):
        path = urlparse(self.path)
        parts = [x for x in path.path.split("/") if x]
        if parts not in (["health"], ["ready"]) and not self.authorized():
            return self.unauthorized()
        if parts == ["ready"]:
            if repository is None:
                return self.backend_unavailable()
            return self.send_json(200, {"ok": True})
        if parts != ["health"] and repository is None:
            return self.backend_unavailable()
        try:
            if parts == ["health"]:
                return self.send_json(200, {"ok": True})
            if parts == ["journal", "status"]:
                return self.send_json(200, repository.journal_status())
            if parts == ["applications"]:
                status = parse_qs(path.query).get("status", [None])[0]
                if status is not None and status not in STATE_TABS:
                    raise ValueError("status must be one of: " + ", ".join(STATE_TABS))
                return self.send_json(200, repository.list_applications(status))
            if parts == ["profile"]:
                return self.send_json(200, repository.get_profile())
            if parts == ["files"]:
                file_path = parse_qs(path.query).get("path", [None])[0]
                if not file_path:
                    raise ValueError("missing required query parameter: path")
                result = repository.read_file(file_path)
                return self.send_json(200, {
                    "file": result["file"],
                    "content_base64": base64.b64encode(result["content"]).decode("ascii"),
                })
            if len(parts) == 2 and parts[0] == "applications":
                app = repository.get_application(parts[1])
                return self.send_json(200, app) if app else self.send_json(404, {"error": "not found"})
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "events":
                return self.send_json(200, repository.list_events(parts[1]))
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "artifacts":
                return self.send_json(200, repository.list_artifacts(parts[1]))
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "research":
                return self.send_json(200, repository.list_research(parts[1]))
            if len(parts) == 4 and parts[0] == "applications" and parts[2] == "artifacts":
                result = repository.read_artifact(parts[1], parts[3])
                return self.send_json(200, {
                    "artifact": result["artifact"],
                    "content_base64": base64.b64encode(result["content"]).decode("ascii"),
                })
            return self.send_json(404, {"error": "not found"})
        except KeyError as exc:
            return self.send_json(404, {"error": "not found", "field": str(exc)})
        except Exception as exc:
            return self.send_exception(exc)

    def _write(self):
        path = urlparse(self.path)
        parts = [x for x in path.path.split("/") if x]
        if not self.authorized():
            return self.unauthorized()
        try:
            data = body(self)
            if parts == ['storage', 'migrate-internal']:
                return self.send_json(200, repository.migrate_internal_storage())
            if parts == ["applications"]:
                app = repository.create_application(
                    required(data, "company"), required(data, "role"), required(data, "posting_url"),
                    data.get("data"), data.get("idempotency_key"))
                return self.send_json(201, app)
            if parts == ["journal", "repair"]:
                return self.send_json(200, repository.repair_journal())
            if parts == ["journal", "migrate-application-states"]:
                return self.send_json(200, repository.migrate_application_states())
            if parts == ["journal", "rollback-ready"]:
                results = []
                for application in repository.list_applications(status="Ready for Auto-Apply"):
                    results.append(repository.rollback_ready_application(application.application_id))
                return self.send_json(200, {"rolled_back": results})
            if parts == ["profile"]:
                return self.send_json(200, repository.save_profile(data))
            if parts == ["files"]:
                content = base64.b64decode(required(data, "content_base64"), validate=True)
                return self.send_json(201, repository.store_file(
                    required(data, "path"), content, data.get("mime_type"), data.get("idempotency_key")
                ))
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "transition":
                app = repository.transition(parts[1], required(data, "target"), data.get("data"), data.get("idempotency_key"))
                return self.send_json(200, app)
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "events":
                event = repository.append_event(parts[1], required(data, "event_type"), data.get("data"), data.get("idempotency_key"))
                return self.send_json(201, event)
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "repair-format":
                return self.send_json(200, repository.repair_application_journal(parts[1]))
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "delete":
                return self.send_json(200, repository.delete_application(parts[1]))
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "artifacts":
                content = base64.b64decode(required(data, "content_base64"), validate=True)
                artifact = repository.store_artifact(
                    parts[1],
                    required(data, "name"),
                    content,
                    data.get("mime_type"),
                    data.get("idempotency_key"),
                )
                return self.send_json(201, artifact)
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "research":
                result = repository.write_research(
                    parts[1],
                    required(data, "title"),
                    required(data, "content"),
                    data.get("idempotency_key"),
                )
                return self.send_json(201, result)
            return self.send_json(404, {"error": "not found"})
        except KeyError as exc:
            return self.send_json(404, {"error": "not found", "field": str(exc)})
        except Exception as exc:
            return self.send_exception(exc)

    def do_POST(self):
        return self._write()

    def do_PUT(self):
        return self._write()


def serve(host="127.0.0.1", port=8080):
    print("persistence API listening on http://{}:{}".format(host, port))
    HTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the job-search persistence API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    serve(args.host, args.port)
