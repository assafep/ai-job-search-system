import base64
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
import urllib.error

import os
import sys
import hmac

from .repository import MemoryRepository
from .journal_format import STATE_TABS, ALL_STATES


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
use_google_backend = should_use_google_backend() or os.environ.get('REPOSITORY_BACKEND') == 'hybrid'
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


def initialize_backend():
    """Retry initialization after explicit setup, including on another instance."""
    global repository, repository_startup_error
    if os.environ.get('REPOSITORY_BACKEND') != 'hybrid':
        return
    from .google_docs_repository import GoogleDocsRepository
    from .google_auth import token_from_environment
    from .hybrid_repository import HybridRepository
    from .initialization import journal_id
    try:
        document = journal_id()
        if not document:
            raise RuntimeError('Storage initialization required')
        repository = HybridRepository(GoogleDocsRepository(token_from_environment(), document,
            os.environ['GOOGLE_ARTIFACT_ROOT_URL'], os.environ.get('GOOGLE_APPLICATION_ROOT_PATH')))
        repository_startup_error = None
    except Exception as exc:
        repository = None
        text = str(exc)
        if "://" in text or "private_key" in text or "Bearer " in text:
            text = type(exc).__name__
        repository_startup_error = text or 'Storage is not initialized or is unavailable'


if os.environ.get('REPOSITORY_BACKEND') == 'hybrid':
    repository = None  # Initialize lazily inside the function invocation.


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
    def log_message(self, format, *args):
        # Request lines belong in the optional API log, never on the terminal.
        from services.api_log import log_access
        log_access(self.address_string(), self.log_date_time_string(), format % args)

    def send_json(self, status, value):
        raw = json.dumps(output(value)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        expected = (os.environ.get("APPLICATION_SERVICE_TOKEN")
                    or os.environ.get("PERSISTENCE_API_TOKEN")
                    or os.environ.get('AJSS_API_TOKEN'))
        if not expected:
            return os.environ.get('REPOSITORY_BACKEND') != 'hybrid'
        return hmac.compare_digest(self.headers.get("Authorization", ''), "Bearer " + expected)

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

    def _public_base(self):
        host = self.headers.get('Host', 'localhost:8080')
        return 'https://' + host

    def _gmail_record(self):
        from .gmail_grant import load_record
        return load_record()

    def _gmail_public(self, parts):
        from .gmail_grant import (
            code_matches, exchange_code, load_record, public_urls,
            renew_page, save_record, apply_refresh_token, start_connect,
        )
        query = parse_qs(urlparse(self.path).query)
        code = (query.get('code') or [''])[0]
        record = load_record()
        base = self._public_base()
        if parts == ['gmail', 'connect']:
            if not code_matches(record, code):
                self.send_response(403)
                self.end_headers()
                return
            urls = public_urls(base, code)
            record, target = start_connect(record, urls['callback_url'])
            save_record(record)
            self.send_response(302)
            self.send_header('Location', target)
            self.end_headers()
            return
        if parts == ['gmail', 'callback']:
            state = (query.get('state') or [''])[0]
            if not code_matches(record, state):
                self.send_response(403)
                self.end_headers()
                return
            urls = public_urls(base, state)
            tokens = exchange_code(
                record['client_id'], record['client_secret'], code, urls['callback_url'],
                verifier=record.get('code_verifier'))
            updated = apply_refresh_token(
                record, tokens['refresh_token'], expires_in=tokens.get('refresh_token_expires_in'))
            save_record(updated)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            raw = b'<p>The new Gmail grant is saved. You can close this page.</p>'
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if parts == ['gmail', 'renew']:
            status, raw = renew_page(record, code, public_urls(base, code or 'x')['callback_url'])
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return None

    def _mcp(self, oauth=False):
        from .mcp_adapter import handle_http
        return handle_http(self, oauth=oauth)

    def _is_mcp_oauth(self):
        path = urlparse(self.path).path
        return (path in ('/authorize', '/token', '/register', '/revoke', '/mcp-oauth/consent')
                or path.startswith('/.well-known/oauth-'))

    def do_GET(self):
        if self._is_mcp_oauth():
            return self._mcp(oauth=True)
        if urlparse(self.path).path.rstrip('/') == '/mcp':
            return self._mcp()
        path = urlparse(self.path)
        parts = [x for x in path.path.split("/") if x]
        if parts == ['internal', 'workflow-schedule']:
            return self.send_json(404, {'error': 'not found'})
        if parts[:1] == ['gmail'] and parts[1:2] != ['grant']:
            return self._gmail_public(parts)
        if parts not in (["health"], ["ready"]) and not self.authorized():
            return self.unauthorized()
        if repository is None and os.environ.get('REPOSITORY_BACKEND') == 'hybrid':
            initialize_backend()
        if parts == ["ready"]:
            if repository is None:
                return self.backend_unavailable()
            try:
                checks = repository.readiness() if hasattr(repository, 'readiness') else {}
                return self.send_json(200, {"ok": True, **checks})
            except Exception:
                return self.send_json(503, {'error': 'persistence readiness check failed'})
        if parts == ["workflows", "execution"]:
            if repository is None and os.environ.get('REPOSITORY_BACKEND') == 'hybrid':
                initialize_backend()
            if repository is None:
                return self.backend_unavailable()
            try:
                we = repository.get_workflow_execution()
                return self.send_json(200, we if we else {'workstation_enabled': True})
            except Exception as exc:
                return self.send_exception(exc)
        if parts != ["health"] and repository is None:
            return self.backend_unavailable()
        try:
            if parts[:1] in (["workflows"], ["workflow-runs"]):
                from .workflow_runs import handle
                return handle(self, 'GET', parts, repository)
            if parts == ["health"]:
                return self.send_json(200, {"ok": True})
            if parts == ["journal", "terminal-items"]:
                from .terminal_journal import terminal_items
                return self.send_json(200, terminal_items(repository))
            if parts == ["journal", "status"]:
                return self.send_json(200, repository.journal_status())
            if parts == ["journal", "tabs"]:
                return self.send_json(200, repository.journal_tabs())
            if parts == ["journal", "queues"]:
                return self.send_json(200, repository.queue_counts())
            if parts == ["applications"]:
                status = parse_qs(path.query).get("status", [None])[0]
                if status is not None and status not in ALL_STATES:
                    raise ValueError("status must be one of: " + ", ".join(ALL_STATES))
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
            if parts[:1] == ["workflow-runs"]:
                if (parts == ["workflow-runs"] or (len(parts) >= 3 and parts[2] == 'chatgpt')) and repository is None:
                    if os.environ.get('REPOSITORY_BACKEND') == 'hybrid':
                        initialize_backend()
                    if repository is None:
                        return self.backend_unavailable()
                from .workflow_runs import handle
                return handle(self, self.command if hasattr(self, 'command') else 'POST', parts, repository, data)
            if parts == ['gmail', 'grant', 'install']:
                from .gmail_grant import install_grant
                for field in ('refresh_token', 'client_id', 'client_secret'):
                    required(data, field)
                return self.send_json(200, install_grant(data))
            if parts == ['gmail', 'grant', 'prepare']:
                from .gmail_grant import load_record, prepare_notice, refresh_access, save_record
                record = load_record()
                if not record.get('refresh_token'):
                    return self.send_json(200, {'status': 'missing'})
                access, record = refresh_access(record)
                updated, message = prepare_notice(record, self._public_base())
                save_record(updated)
                if message is None:
                    return self.send_json(200, {
                        'status': 'current', 'refresh_expires_at': updated.get('refresh_expires_at')})
                return self.send_json(200, {
                    'status': 'due', 'access_token': access.token(), 'message': message,
                    'refresh_expires_at': updated.get('refresh_expires_at')})
            if parts == ['gmail', 'grant', 'sent']:
                from .gmail_grant import acknowledge_notice
                return self.send_json(200, acknowledge_notice())
            if parts == ['setup', 'initialize'] and os.environ.get('REPOSITORY_BACKEND') == 'hybrid':
                from .initialization import initialize
                result = initialize()
                initialize_backend()
                return self.send_json(200, result)
            if repository is None and os.environ.get('REPOSITORY_BACKEND') == 'hybrid':
                initialize_backend()
            if repository is None:
                return self.backend_unavailable()
            if parts == ['workflows', 'execution']:
                if 'workstation_enabled' not in data:
                    return self.send_json(400, {'error': 'workstation_enabled is required'})
                if not isinstance(data['workstation_enabled'], bool):
                    return self.send_json(400, {'error': 'workstation_enabled must be a boolean'})
                result = repository.set_workflow_execution({'workstation_enabled': data['workstation_enabled']})
                return self.send_json(200, result)
            if parts == ['bulk-updates']:
                from .bulk_updates import execute
                return self.send_json(200, execute(repository, data))
            if parts == ['storage', 'migrate-internal']:
                return self.send_json(200, repository.migrate_internal_storage())
            if parts == ["applications"]:
                app = repository.create_application(
                    required(data, "company"), required(data, "role"), required(data, "posting_url"),
                    target_state=data.get("target_state", "Bookmarked"),
                    data=data.get("data"), idempotency_key=data.get("idempotency_key"))
                if app is None:
                    return self.send_json(200, {'capacity_exhausted': True})
                return self.send_json(201, app)
            if parts == ['journal', 'complete-terminal']:
                from .terminal_journal import complete_terminal
                return self.send_json(200, complete_terminal(repository))
            if parts == ["journal", "repair"]:
                return self.send_json(200, repository.repair_journal())
            if parts == ["journal", "migrate-application-states"]:
                return self.send_json(200, repository.migrate_application_states())
            if parts == ["journal", "rollback-prepared"]:
                results = []
                for application in repository.list_applications(status="Prepared"):
                    results.append(repository.rollback_ready_application(application.application_id))
                return self.send_json(200, {"rolled_back": results})
            if parts == ["profile"]:
                return self.send_json(200, repository.save_profile(data))
            if parts == ["files"]:
                content = base64.b64decode(required(data, "content_base64"), validate=True)
                return self.send_json(201, repository.store_file(
                    required(data, "path"), content, data.get("mime_type"), data.get("idempotency_key")
                ))
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "complete":
                from .terminal_journal import complete_application
                result = complete_application(repository, parts[1], required(data, 'reason'))
                return self.send_json(200, result)
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "transition":
                app = repository.transition(parts[1], required(data, "target"), data.get("data"), data.get("idempotency_key"))
                return self.send_json(200, app)
            if len(parts) == 4 and parts[:2] == ['journal', 'tabs'] and parts[3] == 'bookmark':
                return self.send_json(200, repository.bookmark_staged_tab(parts[2], required(data, 'idempotency_key')))
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "events":
                event = repository.append_event(parts[1], required(data, "event_type"), data.get("data"), data.get("idempotency_key"))
                return self.send_json(201, event)
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "repair-format":
                return self.send_json(200, repository.repair_application_journal(parts[1]))
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "update":
                app = repository.update_application(
                    parts[1],
                    company=data.get("company"),
                    role=data.get("role"),
                )
                return self.send_json(200, app)
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "delete":
                return self.send_json(200, repository.delete_application(parts[1]))
            if len(parts) == 3 and parts[0] == "applications" and parts[2] == "delete-last-event":
                return self.send_json(200, repository.delete_last_event(parts[1]))
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
        if self._is_mcp_oauth():
            return self._mcp(oauth=True)
        if urlparse(self.path).path.rstrip('/') == '/mcp':
            return self._mcp()
        parts = [x for x in urlparse(self.path).path.split('/') if x]
        if parts == ['gmail', 'renew']:
            from .gmail_grant import apply_refresh_token, code_matches, load_record, save_record
            length = int(self.headers.get('Content-Length', '0') or '0')
            raw = self.rfile.read(length).decode()
            form = parse_qs(raw)
            record = load_record()
            code = (form.get('code') or [''])[0]
            if not code_matches(record, code):
                self.send_response(403)
                self.end_headers()
                return
            updated = apply_refresh_token(
                record, (form.get('refresh_token') or [''])[0],
                client_id=(form.get('client_id') or [''])[0],
                client_secret=(form.get('client_secret') or [''])[0])
            save_record(updated)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            page = b'<p>The new Gmail grant is saved. You can close this page.</p>'
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        return self._write()

    def do_PUT(self):
        if urlparse(self.path).path.rstrip('/') == '/mcp':
            return self._mcp()
        return self._write()

    def do_OPTIONS(self):
        if self._is_mcp_oauth():
            return self._mcp(oauth=True)
        return self.send_json(405, {'error': 'method not allowed'})

    def do_DELETE(self):
        if urlparse(self.path).path.rstrip('/') == '/mcp':
            return self._mcp()
        return self.send_json(405, {'error': 'method not allowed'})


def _runtime_for_logs():
    from services.runtime_config import load
    path = os.environ.get(
        "RUNTIME_CONFIG_PATH",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config", "runtime.json")),
    )
    if not os.path.exists(path):
        return {}
    try:
        return load(path, require=False)
    except (OSError, ValueError):
        return {}


def configure_activity_log():
    """Turn on the shared activity log for this API process."""
    from services.activity_log import configure_from_runtime
    try:
        configure_from_runtime(_runtime_for_logs())
    except ValueError as error:
        sys.stderr.write(str(error) + "\n")


def configure_api_log():
    """Turn on the Application Service request log. Off unless configured."""
    from services.api_log import configure_from_runtime
    try:
        configure_from_runtime(_runtime_for_logs())
    except ValueError as error:
        sys.stderr.write(str(error) + "\n")


def serve(host="127.0.0.1", port=8080):
    from services.api_log import log_server
    configure_activity_log()
    configure_api_log()
    log_server("Application Service listening on http://{}:{}".format(host, port))
    HTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Application Service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    serve(args.host, args.port)
