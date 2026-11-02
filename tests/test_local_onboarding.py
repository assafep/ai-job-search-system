"""The local-run checklist is repeatable and never prints secrets."""
import json
import os
import stat
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from argparse import Namespace

from ajss.onboard.checklist import (
    apply_settings,
    assess,
    consent_url,
    exchange_code,
    main,
    read_state,
    run_onboard,
    upsert_env,
    wait_for_code,
    write_state,
)

JOURNAL = "https://docs.google.com/document/d/journal-id/edit"
DRIVE = "https://drive.google.com/drive/folders/root-id"
SECRET = "sk-planted-secret"
DATABASE = "postgres://app:planted-password@db.example/ajss"


def environment(**extra):
    return dict(extra)


def test_drive_step_accepts_the_address_bar_and_rejects_share_shortcuts(tmp_path):
    from adapters.google.drive import is_drive_folder_url
    runtime = tmp_path / "runtime.json"
    env_file = tmp_path / ".env"
    assert is_drive_folder_url("https://drive.google.com/drive/u/0/folders/abc")
    assert is_drive_folder_url("https://drive.google.com/drive/folders/abc?usp=sharing")
    assert not is_drive_folder_url("https://drive.google.com/drive/my-drive")
    assert not is_drive_folder_url("https://drive.google.com/open?id=abc")
    report = assess(environment(GOOGLE_CLIENT_ID="client-public", GOOGLE_CLIENT_SECRET="hidden-secret",
                                GOOGLE_REFRESH_TOKEN="refresh-secret"), tmp_path, runtime)
    assert "address bar" in report["next"]["instructions"]
    assert "Do not use Share" in report["next"]["instructions"]
    with pytest.raises(ValueError, match="address bar"):
        apply_settings([("artifact_root_url", "https://drive.google.com/open?id=abc")], tmp_path, runtime, env_file)
    apply_settings([("artifact_root_url", "https://drive.google.com/drive/u/0/folders/abc")], tmp_path, runtime, env_file)
    assert assess(environment(), tmp_path, runtime)["steps"][1]["ready"] is True


def test_first_step_is_google_and_offers_clickable_links(tmp_path):
    report = assess(environment(), tmp_path, tmp_path / "runtime.json")
    assert report["ready"] is False
    assert report["next"]["id"] == "google"
    urls = [link["url"] for link in report["next"]["links"]]
    assert "https://console.cloud.google.com/apis/credentials" in urls
    assert "https://console.cloud.google.com/projectcreate" in urls
    assert "https://console.cloud.google.com/projectselector2/home/dashboard" in urls
    assert "Name the client ajss-local" in report["next"]["instructions"]
    assert "Desktop app" in report["next"]["instructions"]
    assert "Leave the checkbox \"Use this client for an AI-powered agent\" unchecked" in report["next"]["instructions"]
    assert "Create a project only when" in report["next"]["instructions"]
    assert "Web application" in report["next"]["instructions"]
    assert SECRET not in json.dumps(report)


def test_consent_link_appears_when_the_client_id_is_known(tmp_path):
    url = consent_url("client-public.apps.googleusercontent.com")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert query["client_id"] == ["client-public.apps.googleusercontent.com"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/"]
    assert query["scope"] == ["https://www.googleapis.com/auth/drive"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert "include_granted_scopes" not in query
    report = assess(environment(GOOGLE_CLIENT_ID="client-public.apps.googleusercontent.com",
                                GOOGLE_CLIENT_SECRET="hidden-secret"), tmp_path, tmp_path / "runtime.json")
    assert report["next"]["id"] == "google"
    assert any(link["url"].startswith("https://accounts.google.com/") for link in report["next"]["links"])
    assert "hidden-secret" not in json.dumps(report)


def test_refresh_token_completes_google_without_echoing_it(tmp_path):
    env = environment(
        GOOGLE_CLIENT_ID="client-public",
        GOOGLE_CLIENT_SECRET="hidden-secret",
        GOOGLE_REFRESH_TOKEN="refresh-secret",
    )
    report = assess(env, tmp_path, tmp_path / "runtime.json")
    assert report["steps"][0]["ready"] is True
    assert report["next"]["id"] == "drive_root"
    assert "refresh-secret" not in json.dumps(report)


def test_settings_are_idempotent_and_a_new_journal_clears_confirmation(tmp_path):
    runtime = tmp_path / "config" / "runtime.json"
    env_file = tmp_path / ".env"
    root = tmp_path
    apply_settings([
        ("artifact_root_url", DRIVE),
        ("interview_journal_url", JOURNAL),
        ("journal_confirmed", "yes"),
        ("resume_path", "Applicant/resume.pdf"),
        ("database_mode", "local"),
        ("profile_in_drive", "yes"),
        ("resume_in_drive", "yes"),
    ], root, runtime, env_file)
    first_runtime = runtime.read_text(encoding="utf-8")
    first_env = env_file.read_text(encoding="utf-8")
    apply_settings([
        ("artifact_root_url", DRIVE),
        ("interview_journal_url", JOURNAL),
        ("journal_confirmed", "yes"),
        ("resume_path", "Applicant/resume.pdf"),
        ("database_mode", "local"),
    ], root, runtime, env_file)
    assert runtime.read_text(encoding="utf-8") == first_runtime
    assert env_file.read_text(encoding="utf-8") == first_env
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    apply_settings([("interview_journal_url", JOURNAL.replace("journal-id", "other-id"))], root, runtime, env_file)
    state = json.loads((root / "var" / "onboarding-state.json").read_text(encoding="utf-8"))
    assert state["journal_confirmed"] is False


def test_completed_checklist_names_the_start_command(tmp_path):
    runtime = tmp_path / "config" / "runtime.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({"google_drive": {
        "interview_journal_url": JOURNAL,
        "artifact_root_url": DRIVE,
    }}))
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "onboarding-state.json").write_text(json.dumps({
        "journal_confirmed": True,
        "profile_sha256": "a" * 64,
        "profile_missing": [],
        "calendly_declined": True,
        "resume_in_drive": True,
    }))
    env = environment(
        GOOGLE_CLIENT_ID="client-public",
        GOOGLE_CLIENT_SECRET="hidden-secret",
        GOOGLE_REFRESH_TOKEN="refresh-secret",
        DATABASE_URL=DATABASE,
        GOOGLE_RESUME_DRIVE_PATH="Applicant/resume.pdf",
        OPENAI_API_KEY=SECRET,
        GOOGLE_GMAIL_REFRESH_TOKEN="gmail-refresh-secret",
        GOOGLE_CALENDAR_REFRESH_TOKEN="calendar-refresh-secret",
    )
    report = assess(env, tmp_path, runtime)
    assert report["ready"] is True
    assert report["next"] is None
    assert report["start_when_ready"] == "python -m ajss.cli run"
    text = json.dumps(report)
    assert SECRET not in text
    assert "planted-password" not in text
    assert "hidden-secret" not in text
    assert "gmail-refresh-secret" not in text
    assert "calendar-refresh-secret" not in text


def test_secrets_cannot_be_set_on_the_command_line(tmp_path):
    with pytest.raises(ValueError, match="unsupported"):
        apply_settings([("OPENAI_API_KEY", SECRET)], tmp_path, tmp_path / "runtime.json", tmp_path / ".env")


def test_env_write_falls_back_when_the_file_cannot_be_replaced(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("KEEP=yes\n", encoding="utf-8")

    def refuse(source, destination):
        raise OSError("Device or resource busy")

    monkeypatch.setattr(os, "replace", refuse)
    upsert_env(path, {"GOOGLE_RESUME_DRIVE_PATH": "Applicant/resume.pdf"})
    text = path.read_text(encoding="utf-8")
    assert "KEEP=yes" in text
    assert 'GOOGLE_RESUME_DRIVE_PATH="Applicant/resume.pdf"' in text or "GOOGLE_RESUME_DRIVE_PATH=Applicant/resume.pdf" in text


def test_older_resume_path_name_still_counts_until_it_is_replaced(tmp_path):
    from ajss.settings import resume_drive_path
    assert resume_drive_path({"AJSS_RESUME_DRIVE_PATH": "Applicant/old.pdf"}) == "Applicant/old.pdf"
    assert resume_drive_path({
        "GOOGLE_RESUME_DRIVE_PATH": "Applicant/new.pdf",
        "AJSS_RESUME_DRIVE_PATH": "Applicant/old.pdf",
    }) == "Applicant/new.pdf"
    env_file = tmp_path / ".env"
    upsert_env(env_file, {"AJSS_RESUME_DRIVE_PATH": "Applicant/old.pdf"})
    apply_settings([("resume_path", "Applicant/resume.pdf")], tmp_path, tmp_path / "runtime.json", env_file)
    text = env_file.read_text(encoding="utf-8")
    assert "GOOGLE_RESUME_DRIVE_PATH=Applicant/resume.pdf" in text
    assert "AJSS_RESUME_DRIVE_PATH" not in text


def test_env_file_replaces_one_key_and_keeps_the_others(tmp_path):
    path = tmp_path / ".env"
    upsert_env(path, {"OPENAI_API_KEY": "one", "DATABASE_URL": DATABASE})
    upsert_env(path, {"OPENAI_API_KEY": "two"})
    text = path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=two" in text
    assert "OPENAI_API_KEY=one" not in text
    assert "planted-password" in text
    upsert_env(path, {"OPENAI_API_KEY": "two", "DATABASE_URL": DATABASE})
    assert path.read_text(encoding="utf-8") == text


def test_report_command_does_not_print_a_stored_secret(tmp_path, capsys):
    upsert_env(tmp_path / ".env", {"OPENAI_API_KEY": SECRET, "DATABASE_URL": DATABASE})
    code = main(["--root", str(tmp_path)])
    output = capsys.readouterr().out
    assert code == 0
    assert SECRET not in output
    assert "planted-password" not in output
    assert json.loads(output)["next"]["id"]


def test_check_exits_when_setup_is_incomplete(tmp_path):
    assert main(["--check", "--root", str(tmp_path)]) == 2


def test_onboard_creates_the_profile_from_the_resume(tmp_path, capsys, monkeypatch):
    for name in (
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
        "GOOGLE_GMAIL_REFRESH_TOKEN", "GOOGLE_CALENDAR_REFRESH_TOKEN",
        "DATABASE_URL", "AJSS_DATABASE_URL", "AJSS_ALLOW_LOCAL_SQLITE",
        "GOOGLE_RESUME_DRIVE_PATH", "AJSS_RESUME_DRIVE_PATH", "OPENAI_API_KEY", "XAI_API_KEY",
        "AJSS_LLM_API_KEY", "AJSS_LLM_PROVIDER", "AJSS_LLM_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    runtime = tmp_path / "config" / "runtime.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({"google_drive": {
        "interview_journal_url": JOURNAL,
        "artifact_root_url": DRIVE,
    }}), encoding="utf-8")
    write_state(tmp_path, {"journal_confirmed": True, "resume_in_drive": True, "profile_sha256": "b" * 64})
    upsert_env(tmp_path / ".env", {
        "GOOGLE_CLIENT_ID": "client-public",
        "GOOGLE_CLIENT_SECRET": "hidden-secret",
        "GOOGLE_REFRESH_TOKEN": "refresh-secret",
        "DATABASE_URL": DATABASE,
        "GOOGLE_RESUME_DRIVE_PATH": "Applicant/resume.pdf",
        "OPENAI_API_KEY": SECRET,
    })
    apply_settings([("resume_path", "Applicant/other.pdf")], tmp_path, runtime, tmp_path / ".env")
    assert "profile_sha256" not in read_state(tmp_path)
    calls = []

    def publisher(root, env, runtime_path):
        calls.append(env["GOOGLE_RESUME_DRIVE_PATH"])
        state = read_state(root)
        state["profile_sha256"] = "c" * 64
        state["profile_missing"] = ["phone"]
        write_state(root, state)

    before = assess(
        {
            "GOOGLE_CLIENT_ID": "client-public",
            "GOOGLE_CLIENT_SECRET": "hidden-secret",
            "GOOGLE_REFRESH_TOKEN": "refresh-secret",
            "DATABASE_URL": DATABASE,
            "GOOGLE_RESUME_DRIVE_PATH": "Applicant/other.pdf",
            "OPENAI_API_KEY": SECRET,
        },
        tmp_path,
        runtime,
    )
    assert before["next"]["id"] == "profile"
    assert "You do not provide this file" in before["next"]["instructions"]
    code = run_onboard(Namespace(
        root=str(tmp_path),
        set=[
            "wizard=yes",
            "use=google", "use=drive_root", "use=journal", "use=journal_confirmed",
            "use=database", "use=resume_path", "use=resume_in_drive",
            "use=llm_provider", "use=llm_model", "use=llm_key",
        ],
        listen=False, check=False,
    ), publisher=publisher)
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert calls == ["Applicant/other.pdf"]
    assert report["gate"] == "run"
    assert report["next"]["id"] == "contact_email"
    assert report["next"]["mode"] == "confirm"
    assert "email address" in report["next"]["instructions"]
    assert report["ready"] is False
    assert SECRET not in json.dumps(report)


def test_missing_contacts_are_requested_without_printing_them(tmp_path, capsys, monkeypatch):
    phone = "+1-555-0109"
    for name in (
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
        "GOOGLE_GMAIL_REFRESH_TOKEN", "GOOGLE_CALENDAR_REFRESH_TOKEN",
        "DATABASE_URL", "AJSS_DATABASE_URL", "AJSS_ALLOW_LOCAL_SQLITE",
        "GOOGLE_RESUME_DRIVE_PATH", "AJSS_RESUME_DRIVE_PATH", "OPENAI_API_KEY", "XAI_API_KEY",
        "AJSS_LLM_API_KEY", "AJSS_LLM_PROVIDER", "AJSS_LLM_MODEL",
        "AJSS_CONTACT_EMAIL", "AJSS_CONTACT_PHONE",
    ):
        monkeypatch.delenv(name, raising=False)
    runtime = tmp_path / "config" / "runtime.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({"google_drive": {
        "interview_journal_url": JOURNAL,
        "artifact_root_url": DRIVE,
    }}), encoding="utf-8")
    write_state(tmp_path, {
        "journal_confirmed": True,
        "resume_in_drive": True,
        "profile_sha256": "d" * 64,
        "profile_missing": ["email", "phone"],
        "contacts": {"phone": phone},
    })
    env = {
        "GOOGLE_CLIENT_ID": "client-public",
        "GOOGLE_CLIENT_SECRET": "hidden-secret",
        "GOOGLE_REFRESH_TOKEN": "refresh-secret",
        "DATABASE_URL": DATABASE,
        "GOOGLE_RESUME_DRIVE_PATH": "Applicant/resume.pdf",
        "OPENAI_API_KEY": SECRET,
        "GOOGLE_GMAIL_REFRESH_TOKEN": "gmail-refresh-secret",
        "GOOGLE_CALENDAR_REFRESH_TOKEN": "calendar-refresh-secret",
    }
    report = assess(env, tmp_path, runtime)
    assert report["next"]["id"] == "contact_email"
    assert "What is your email address?" in report["next"]["instructions"]
    assert phone not in json.dumps(report)
    apply_settings([("calendly_url", "https://calendly.com/ada/intro")], tmp_path, runtime, tmp_path / ".env")
    saved = json.loads(runtime.read_text(encoding="utf-8"))
    assert saved["google_drive"]["interview_journal_url"] == JOURNAL
    assert "scheduling" not in saved
    assert read_state(tmp_path)["calendly_url"] == "https://calendly.com/ada/intro"
    with pytest.raises(ValueError, match="calendly.com"):
        apply_settings([("calendly_url", "https://example.com/meet")], tmp_path, runtime, tmp_path / ".env")
    apply_settings([("calendly_url", "none")], tmp_path, runtime, tmp_path / ".env")
    assert "scheduling" not in json.loads(runtime.read_text(encoding="utf-8"))
    assert "calendly_url" not in read_state(tmp_path)
    assert read_state(tmp_path)["calendly_declined"] is True
    with pytest.raises(ValueError, match="email address"):
        apply_settings([("contact_email", "not-an-email")], tmp_path, runtime, tmp_path / ".env")

    upsert_env(tmp_path / ".env", env)
    write_state(tmp_path, {
        "journal_confirmed": True,
        "resume_in_drive": True,
        "profile_sha256": "d" * 64,
        "profile_missing": ["phone"],
        "calendly_declined": True,
    })

    def publisher(root, got_env, runtime_path):
        state = read_state(root)
        assert state["contacts"]["phone"] == phone
        state["profile_missing"] = []
        write_state(root, state)

    code = run_onboard(
        Namespace(root=str(tmp_path), set=[
            "wizard=yes",
            "use=google", "use=drive_root", "use=journal", "use=journal_confirmed",
            "use=database", "use=resume_path", "use=resume_in_drive",
            "use=llm_provider", "use=llm_model", "use=llm_key",
            "use=profile", "use=contact_email", "use=calendly",
            "contact_phone=" + phone,
        ], listen=False, check=False),
        publisher=publisher,
        sanity=lambda *args, **kwargs: {"ok": True, "checks": []},
    )
    output = capsys.readouterr().out
    assert code == 0
    assert json.loads(output)["ready"] is True
    assert phone not in output
    assert "gmail-refresh-secret" not in output
    assert "calendar-refresh-secret" not in output


def _step(report, identifier):
    return next(item for item in report["steps"] if item["id"] == identifier)


def test_onboard_session_answers_twice_from_one_process(tmp_path):
    from ajss.onboard.session import serve, transact
    thread = threading.Thread(
        target=serve, kwargs={"root": tmp_path, "reload": False}, daemon=True,
    )
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            pong = transact(tmp_path, {"ping": True}, timeout=1)
            break
        except OSError:
            time.sleep(0.05)
    else:
        raise AssertionError("onboard session did not start")
    assert pong["pong"] is True
    first = transact(tmp_path, {"set": [], "listen": False, "check": True})
    second = transact(tmp_path, {"set": [], "listen": False, "check": True})
    assert first["code"] == 2
    assert second["code"] == 2
    assert "Google account" in second["output"]


def test_calendly_step_asks_for_the_link(tmp_path):
    runtime = tmp_path / "config" / "runtime.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({"google_drive": {
        "interview_journal_url": JOURNAL,
        "artifact_root_url": DRIVE,
    }}), encoding="utf-8")
    write_state(tmp_path, {
        "journal_confirmed": True,
        "resume_in_drive": True,
        "profile_sha256": "a" * 64,
        "profile_missing": [],
    })
    env = environment(
        GOOGLE_CLIENT_ID="client-public",
        GOOGLE_CLIENT_SECRET="hidden-secret",
        GOOGLE_REFRESH_TOKEN="refresh-secret",
        DATABASE_URL=DATABASE,
        GOOGLE_RESUME_DRIVE_PATH="Applicant/resume.pdf",
        OPENAI_API_KEY=SECRET,
    )
    report = assess(env, tmp_path, runtime)
    step = report["next"]
    assert step["id"] == "calendly"
    assert "What is your Calendly link?" in step["instructions"]
    assert "applicant_profile.json" in step["instructions"]
    assert "--set" not in step["instructions"]
    assert SECRET not in json.dumps(report)


def test_wizard_remembers_a_file_it_already_read_or_wrote():
    from ajss.onboard.checklist import WarmDrive
    downloads = []

    class Store:
        def read_path(self, path):
            downloads.append(path)
            if path.endswith("missing.json"):
                raise KeyError(path)
            return {"content": b'{"personal":{"name":"Ada"}}', "file": {"id": "file-1"}}

        def save_path(self, path, content, mime_type=None, idempotency_key=None):
            downloads.append("save:" + path)
            return {"id": "file-1"}

    warm = WarmDrive(Store())
    first = warm.read_path("Applicant/applicant_profile.json")
    second = warm.read_path("Applicant/applicant_profile.json")
    assert first["content"] == second["content"] == b'{"personal":{"name":"Ada"}}'
    assert downloads == ["Applicant/applicant_profile.json"]
    warm.save_path(
        "Applicant/applicant_profile.json",
        b'{"personal":{"name":"Ada","phone":"1"}}',
        mime_type="application/json",
    )
    assert warm.read_path("Applicant/applicant_profile.json")["content"] == b'{"personal":{"name":"Ada","phone":"1"}}'
    assert downloads == [
        "Applicant/applicant_profile.json",
        "save:Applicant/applicant_profile.json",
    ]
    with pytest.raises(KeyError):
        warm.read_path("Applicant/missing.json")
    with pytest.raises(KeyError):
        warm.read_path("Applicant/missing.json")
    assert downloads.count("Applicant/missing.json") == 2


def test_drive_connection_is_reused_and_a_dropped_socket_is_dialed_once():
    import http.client
    import urllib.error
    from adapters.google.drive import DriveTransport
    connections = []

    class Response:
        def __init__(self, status, body, headers=None):
            self.status = status
            self.reason = "ok"
            self.headers = headers or {}
            self._body = body

        def read(self):
            return self._body

    class Connection:
        def __init__(self, host):
            self.host = host
            self.calls = 0
            self.closed = False
            connections.append(self)

        def request(self, method, path, body=None, headers=None):
            self.calls += 1
            if len(connections) == 1 and self.calls == 1:
                raise http.client.RemoteDisconnected("idle connection closed")
            self._response = Response(200, b"kept")

        def getresponse(self):
            return self._response

        def close(self):
            self.closed = True

    transport = DriveTransport(timeout=5, connector=Connection)
    assert transport.exchange("GET", "https://www.googleapis.com/drive/v3/files/1") == b"kept"
    assert transport.exchange("PATCH", "https://www.googleapis.com/drive/v3/files/1", b"{}") == b"kept"
    assert len(connections) == 2
    assert connections[1].calls == 2
    assert getattr(connections[0], "closed", False) is True

    class Denied(Connection):
        def request(self, method, path, body=None, headers=None):
            self.calls += 1
            self._response = Response(404, b"missing")

    denied = DriveTransport(timeout=5, connector=Denied)
    with pytest.raises(urllib.error.HTTPError):
        denied.exchange("GET", "https://www.googleapis.com/drive/v3/files/missing")
    assert denied._connections["www.googleapis.com"].calls == 1


def test_wizard_opens_one_drive_client_for_later_steps(tmp_path, monkeypatch):
    created = []

    class Store:
        def __init__(self, token, url):
            created.append((token, url))

    monkeypatch.setattr("adapters.google.drive.GoogleDriveArtifactStore", Store)
    monkeypatch.setattr("adapters.google.auth.token_from_environment", lambda: "token")
    runtime = tmp_path / "config" / "runtime.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({"google_drive": {
        "interview_journal_url": JOURNAL,
        "artifact_root_url": DRIVE,
    }}), encoding="utf-8")
    upsert_env(tmp_path / ".env", {
        "GOOGLE_CLIENT_ID": "client-public",
        "GOOGLE_CLIENT_SECRET": "hidden-secret",
        "GOOGLE_REFRESH_TOKEN": "refresh-secret",
    })
    resources = {}
    args = Namespace(root=str(tmp_path), set=[], listen=False, check=True)
    assert run_onboard(args, resources=resources) == 2
    assert run_onboard(args, resources=resources) == 2
    assert created == [("token", DRIVE)]


def test_profile_publish_reuses_the_drive_client(tmp_path, monkeypatch):
    from ajss.onboard.checklist import publish_applicant_profile
    created = []

    class Store:
        def __init__(self, token, url):
            created.append(token)

        def read_path(self, path):
            return {"content": json.dumps({
                "personal": {"name": "Ada Applicant", "email": "ada@example.test"},
                "source_resume": {"path": "Applicant/resume.pdf", "sha256": "abc"},
            }).encode()}

        def save_path(self, path, content, mime_type=None, idempotency_key=None):
            return {"id": path}

    monkeypatch.setattr("adapters.google.drive.GoogleDriveArtifactStore", Store)
    monkeypatch.setattr("adapters.google.auth.token_from_environment", lambda: "token")
    runtime = tmp_path / "config" / "runtime.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({"google_drive": {
        "interview_journal_url": JOURNAL,
        "artifact_root_url": DRIVE,
    }}), encoding="utf-8")
    write_state(tmp_path, {
        "profile_sha256": "abc",
        "profile_missing": ["phone"],
        "contacts": {"phone": "+1-555-0100"},
    })
    env = {
        "GOOGLE_REFRESH_TOKEN": "refresh",
        "GOOGLE_CLIENT_ID": "client",
        "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_RESUME_DRIVE_PATH": "Applicant/resume.pdf",
    }
    resources = {}
    publish_applicant_profile(tmp_path, env, runtime, resources=resources)
    publish_applicant_profile(tmp_path, env, runtime, resources=resources)
    assert created == ["token"]


def test_publish_writes_the_calendly_link_onto_the_profile(tmp_path, monkeypatch):
    from ajss.onboard.checklist import publish_applicant_profile
    from ajss.onboard.profile import PROFILE_PATH
    saved = {}

    class Store:
        def __init__(self, token, url):
            pass

        def read_path(self, path):
            if path in saved:
                return {"content": saved[path]}
            return {"content": json.dumps({
                "personal": {"name": "Ada Applicant", "email": "ada@example.test"},
                "source_resume": {"path": "Applicant/resume.pdf", "sha256": "abc"},
            }).encode()}

        def save_path(self, path, content, mime_type=None, idempotency_key=None):
            saved[path] = content
            return {"id": path}

    monkeypatch.setattr("adapters.google.drive.GoogleDriveArtifactStore", Store)
    monkeypatch.setattr("adapters.google.auth.token_from_environment", lambda: "token")
    link = "https://calendly.com/ada/intro"
    runtime = tmp_path / "config" / "runtime.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({
        "google_drive": {
            "interview_journal_url": JOURNAL,
            "artifact_root_url": DRIVE,
        },
        "scheduling": {"calendly_url": "https://calendly.com/old/runtime"},
    }), encoding="utf-8")
    write_state(tmp_path, {
        "profile_sha256": "abc",
        "profile_missing": [],
        "calendly_url": link,
    })
    publish_applicant_profile(tmp_path, {
        "GOOGLE_REFRESH_TOKEN": "refresh",
        "GOOGLE_CLIENT_ID": "client",
        "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_RESUME_DRIVE_PATH": "Applicant/resume.pdf",
    }, runtime, resources={})
    document = json.loads(saved[PROFILE_PATH].decode())
    assert document["personal"]["calendly_url"] == link
    assert document["personal"]["email"] == "ada@example.test"
    assert read_state(tmp_path)["calendly_on_profile"] == link
    stored = json.loads(runtime.read_text(encoding="utf-8"))
    assert "calendly_url" not in (stored.get("scheduling") or {})
    assert stored["google_drive"]["artifact_root_url"] == DRIVE


def test_provider_choice_writes_the_name_and_shows_one_key_link(tmp_path):
    from ajss.onboard.checklist import parse_env_file
    runtime = tmp_path / "runtime.json"
    env_file = tmp_path / ".env"
    report = assess(environment(), tmp_path, runtime)
    step = _step(report, "llm_provider")
    assert step["ready"] is False
    assert "grok" in step["instructions"]
    assert "anthropic" in step["instructions"]
    assert "gemini" in step["instructions"]
    assert "local" in step["instructions"]
    assert step["links"] == []
    apply_settings([("llm_provider", "Grok")], tmp_path, runtime, env_file)
    saved = parse_env_file(env_file)
    assert saved["AJSS_LLM_PROVIDER"] == "grok"
    chosen = assess(saved, tmp_path, runtime)
    assert _step(chosen, "llm_provider")["ready"] is True
    key = _step(chosen, "llm_key")
    assert key["ready"] is False
    assert [link["url"] for link in key["links"]] == [
        "https://console.x.ai/team/default/api-keys",
    ]
    assert "AJSS_LLM_API_KEY" in key["instructions"]
    assert "AJSS_LLM_PROVIDER is grok" in key["instructions"]
    assert "Name the key ajss-local" in key["instructions"]
    assert "https://api.x.ai/v1" in key["instructions"]
    with pytest.raises(ValueError, match="named provider"):
        apply_settings([("llm_key", "none")], tmp_path, runtime, env_file)


def test_local_provider_stores_the_base_url_and_can_skip_the_key(tmp_path):
    from ajss.onboard.checklist import parse_env_file
    runtime = tmp_path / "runtime.json"
    env_file = tmp_path / ".env"
    apply_settings([("llm_provider", "custom")], tmp_path, runtime, env_file)
    assert parse_env_file(env_file)["AJSS_LLM_PROVIDER"] == "local"
    waiting = assess(parse_env_file(env_file), tmp_path, runtime)
    provider = _step(waiting, "llm_provider")
    assert provider["ready"] is False
    assert "llm_base_url" in provider["instructions"]
    assert "host.docker.internal" in provider["instructions"]
    apply_settings(
        [("llm_base_url", "http://host.docker.internal:11434/v1/")],
        tmp_path, runtime, env_file,
    )
    assert parse_env_file(env_file)["AJSS_LLM_PROVIDER"] == "http://host.docker.internal:11434/v1"
    named = assess(parse_env_file(env_file), tmp_path, runtime)
    assert _step(named, "llm_provider")["ready"] is True
    assert _step(named, "llm_model")["ready"] is False
    apply_settings([("llm_model", "llama3.2")], tmp_path, runtime, env_file)
    keyed = assess(parse_env_file(env_file), tmp_path, runtime)
    assert _step(keyed, "llm_model")["ready"] is True
    assert _step(keyed, "llm_key")["ready"] is False
    assert "ajss-local" in _step(keyed, "llm_key")["instructions"]
    apply_settings([("llm_key", "none")], tmp_path, runtime, env_file)
    done = assess(parse_env_file(env_file), tmp_path, runtime)
    assert _step(done, "llm_key")["ready"] is True
    assert "AJSS_LLM_API_KEY" not in parse_env_file(env_file)
    with pytest.raises(ValueError, match="AJSS_LLM_API_KEY"):
        apply_settings([("AJSS_LLM_API_KEY", SECRET)], tmp_path, runtime, env_file)
    with pytest.raises(ValueError, match="no user"):
        apply_settings(
            [("llm_base_url", "http://user:secret@127.0.0.1:11434/v1?token=secret")],
            tmp_path, runtime, env_file,
        )


def test_unknown_provider_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="llm_provider must be"):
        apply_settings(
            [("llm_provider", "spacexai")],
            tmp_path, tmp_path / "runtime.json", tmp_path / ".env",
        )


def test_legacy_xai_key_completes_the_model_steps(tmp_path):
    ready = assess(environment(XAI_API_KEY="xai-secret"), tmp_path, tmp_path / "runtime.json")
    assert next(item for item in ready["steps"] if item["id"] == "llm_key")["ready"] is True
    assert next(item for item in ready["steps"] if item["id"] == "llm_provider")["ready"] is True
    assert "xai-secret" not in json.dumps(ready)


def test_authorization_code_is_exchanged_without_a_browser():
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"refresh_token":"refresh-1"}'

    def opener(request):
        seen["body"] = request.data.decode()
        return Response()

    assert exchange_code("code-1", "client-public", "hidden-secret", "http://127.0.0.1:8765/", opener) == "refresh-1"
    assert "code-1" in seen["body"]
    assert "hidden-secret" in seen["body"]


def test_loopback_accepts_one_redirect():
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    result = []

    def serve():
        result.append(wait_for_code(port, 5))

    thread = threading.Thread(target=serve)
    thread.start()
    for _ in range(50):
        try:
            urllib.request.urlopen("http://127.0.0.1:{}/?code=from-google".format(port), timeout=1)
            break
        except OSError:
            time.sleep(0.02)
    thread.join(5)
    assert result == ["from-google"]


def _json_values(text):
    decoder = json.JSONDecoder()
    index = 0
    found = []
    while True:
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, length = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        found.append(value)
        index = start + length
    return found


def _drive_runtime(tmp_path):
    runtime = tmp_path / "config" / "runtime.json"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(json.dumps({"google_drive": {
        "interview_journal_url": JOURNAL,
        "artifact_root_url": DRIVE,
    }}), encoding="utf-8")
    return runtime


def test_read_consent_keeps_the_drive_scope_separate():
    from adapters.google_events import SCOPES
    drive = urllib.parse.parse_qs(urllib.parse.urlsplit(consent_url("client-public")).query)
    gmail = urllib.parse.parse_qs(urllib.parse.urlsplit(
        consent_url("client-public", scope=SCOPES["gmail"], include_granted=True)
    ).query)
    calendar = urllib.parse.parse_qs(urllib.parse.urlsplit(
        consent_url("client-public", scope=SCOPES["calendar"], include_granted=True)
    ).query)
    assert drive["scope"] == ["https://www.googleapis.com/auth/drive"]
    assert "include_granted_scopes" not in drive
    assert gmail["scope"] == [SCOPES["gmail"]]
    assert gmail["include_granted_scopes"] == ["true"]
    assert calendar["scope"] == [SCOPES["calendar"]]
    assert calendar["prompt"] == ["consent"]
    assert calendar["access_type"] == ["offline"]


def test_exchange_grant_returns_the_scope_and_exchange_code_returns_the_token():
    from ajss.onboard.checklist import exchange_grant

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return (
                b'{"refresh_token":"refresh-1",'
                b'"scope":"https://www.googleapis.com/auth/gmail.readonly"}'
            )

    refresh, scope = exchange_grant(
        "code-1", "client-public", "hidden-secret", "http://127.0.0.1:8765/", lambda request: Response(),
    )
    assert refresh == "refresh-1"
    assert scope == "https://www.googleapis.com/auth/gmail.readonly"


def test_a_named_stage_runs_alone_and_reuses_the_drive_client(tmp_path, monkeypatch, capsys):
    from ajss.onboard.session import parse_call
    created = []

    class Store:
        def __init__(self, token, url):
            created.append((token, url))

    monkeypatch.setattr("adapters.google.drive.GoogleDriveArtifactStore", Store)
    monkeypatch.setattr("adapters.google.auth.token_from_environment", lambda: "token")
    _drive_runtime(tmp_path)
    upsert_env(tmp_path / ".env", {
        "GOOGLE_CLIENT_ID": "client-public",
        "GOOGLE_CLIENT_SECRET": "hidden-secret",
        "GOOGLE_REFRESH_TOKEN": "drive-refresh",
    })
    resources = {}
    args = Namespace(root=str(tmp_path), set=[], listen=False, check=False, stage="gmail")

    def schema(url):
        raise AssertionError("database opened")

    assert run_onboard(args, resources=resources, schema=schema) == 0
    first = json.loads(capsys.readouterr().out)
    assert run_onboard(args, resources=resources, schema=schema) == 0
    second = json.loads(capsys.readouterr().out)
    assert created == [("token", DRIVE)]
    assert resources["store"] is not None
    for report in (first, second):
        assert report["gate"] == "run"
        assert report["stage"] == "gmail"
        assert report["can_start"] is False
        assert report["sanity"] is None
        assert report["next"]["id"] == "gmail"
        assert report["next"]["mode"] == "retrieve"
        assert "Do you want to proceed" not in report.get("opening", "")
        assert "view your email messages and settings" in report["next"]["instructions"]
        assert "does not send mail" in report["next"]["instructions"]
        assert "ajss-local" in report["next"]["instructions"]
        assert "make onboard-listen ONBOARD_ARGS='--stage gmail'" in report["next"]["instructions"]
        urls = [link["url"] for link in report["next"]["links"]]
        assert "https://console.cloud.google.com/apis/library/gmail.googleapis.com" in urls
        assert any("gmail.readonly" in url and "include_granted_scopes=true" in url for url in urls)
        assert "drive-refresh" not in json.dumps(report)
        assert "hidden-secret" not in json.dumps(report)
    calendar = Namespace(root=str(tmp_path), set=[], listen=False, check=False, stage="calendar")
    assert run_onboard(calendar, resources=resources, schema=schema) == 0
    calendar_report = json.loads(capsys.readouterr().out)
    assert calendar_report["next"]["id"] == "calendar"
    assert "view events on your calendars" in calendar_report["next"]["instructions"]
    assert "does not create or change events" in calendar_report["next"]["instructions"]
    assert created == [("token", DRIVE)]
    assert parse_call(["--stage", "calendar", "--set", "use=calendar"])["stage"] == "calendar"
    with pytest.raises(ValueError, match="unknown setup step"):
        run_onboard(Namespace(root=str(tmp_path), set=[], listen=False, check=False, stage="nope"))


def test_read_grant_is_stored_apart_from_the_drive_token(tmp_path, capsys):
    from adapters.google_events import SCOPES
    from ajss.onboard.checklist import parse_env_file
    from ajss.onboard.permissions import clear_grant, file_path, granted, save_grant
    env_file = tmp_path / ".env"
    upsert_env(env_file, {
        "GOOGLE_CLIENT_ID": "client-public",
        "GOOGLE_CLIENT_SECRET": "hidden-secret",
        "GOOGLE_REFRESH_TOKEN": "drive-refresh",
    })
    with pytest.raises(RuntimeError, match="read-only"):
        save_grant(tmp_path, env_file, "gmail", "client-public", "hidden-secret", "gmail-refresh", "openid")
    assert "GOOGLE_GMAIL_REFRESH_TOKEN" not in parse_env_file(env_file)
    save_grant(
        tmp_path, env_file, "gmail", "client-public", "hidden-secret",
        "gmail-refresh", SCOPES["gmail"],
    )
    stored = parse_env_file(env_file)
    assert stored["GOOGLE_REFRESH_TOKEN"] == "drive-refresh"
    assert stored["GOOGLE_GMAIL_REFRESH_TOKEN"] == "gmail-refresh"
    assert stat.S_IMODE(file_path(tmp_path, "gmail").stat().st_mode) == 0o600
    assert granted({"GOOGLE_CLIENT_ID": "client-public"}, tmp_path, "gmail") is True
    report = assess(stored, tmp_path, tmp_path / "runtime.json")
    text = json.dumps(report)
    assert next(step for step in report["steps"] if step["id"] == "gmail")["ready"] is True
    assert "gmail-refresh" not in text
    assert "hidden-secret" not in text
    clear_grant(tmp_path, env_file, "gmail")
    assert "GOOGLE_GMAIL_REFRESH_TOKEN" not in parse_env_file(env_file)
    assert parse_env_file(env_file)["GOOGLE_REFRESH_TOKEN"] == "drive-refresh"
    assert not file_path(tmp_path, "gmail").exists()
    with pytest.raises(ValueError, match="unsupported"):
        apply_settings([("GOOGLE_GMAIL_REFRESH_TOKEN", "gmail-refresh")], tmp_path, tmp_path / "runtime.json", env_file)


def test_gmail_listen_does_not_replace_the_drive_token(tmp_path, monkeypatch, capsys):
    from adapters.google_events import SCOPES
    from ajss.onboard.checklist import parse_env_file

    class Store:
        def __init__(self, token, url):
            return None

    monkeypatch.setattr("adapters.google.drive.GoogleDriveArtifactStore", Store)
    monkeypatch.setattr("adapters.google.auth.token_from_environment", lambda: "token")
    monkeypatch.setattr("ajss.onboard.checklist.wait_for_code", lambda port, timeout: "code-1")
    _drive_runtime(tmp_path)
    upsert_env(tmp_path / ".env", {
        "GOOGLE_CLIENT_ID": "client-public",
        "GOOGLE_CLIENT_SECRET": "hidden-secret",
        "GOOGLE_REFRESH_TOKEN": "drive-refresh",
    })

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.body

    bodies = [
        b'{"refresh_token":"gmail-refresh","scope":"https://www.googleapis.com/auth/drive"}',
        (
            b'{"refresh_token":"gmail-refresh","scope":"'
            + SCOPES["gmail"].encode()
            + b'"}'
        ),
    ]

    def opener(request):
        return Response(bodies.pop(0))

    with pytest.raises(RuntimeError, match="read-only"):
        run_onboard(
            Namespace(root=str(tmp_path), set=[], listen=True, check=False, stage="gmail"),
            opener=opener,
        )
    assert parse_env_file(tmp_path / ".env")["GOOGLE_REFRESH_TOKEN"] == "drive-refresh"
    assert "GOOGLE_GMAIL_REFRESH_TOKEN" not in parse_env_file(tmp_path / ".env")
    code = run_onboard(
        Namespace(root=str(tmp_path), set=[], listen=True, check=False, stage="gmail"),
        opener=opener,
    )
    output = capsys.readouterr().out
    assert code == 0
    stored = parse_env_file(tmp_path / ".env")
    assert stored["GOOGLE_REFRESH_TOKEN"] == "drive-refresh"
    assert stored["GOOGLE_GMAIL_REFRESH_TOKEN"] == "gmail-refresh"
    values = _json_values(output)
    assert values[0]["stage"] == "gmail"
    assert "gmail.readonly" in values[0]["open_this_url"]
    assert values[-1]["opening"] == "Read-only Gmail access is already stored."
    assert values[-1]["can_start"] is False
    assert values[-1]["next"] is None
    assert "gmail-refresh" not in output
    assert "drive-refresh" not in output
    assert "hidden-secret" not in output


def test_a_calendar_file_grant_needs_the_read_scope(tmp_path):
    from adapters.google_events import SCOPES
    from ajss.onboard.permissions import file_path, granted
    path = file_path(tmp_path, "calendar")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "refresh_token": "calendar-refresh",
        "client_id": "client-public",
        "client_secret": "hidden-secret",
        "scope": "https://www.googleapis.com/auth/calendar",
    }), encoding="utf-8")
    assert granted({}, tmp_path, "calendar") is False
    path.write_text(json.dumps({
        "refresh_token": "calendar-refresh",
        "scope": SCOPES["calendar"],
    }), encoding="utf-8")
    assert granted({}, tmp_path, "calendar") is True
    report = assess({}, tmp_path, tmp_path / "missing-runtime.json")
    assert next(step for step in report["steps"] if step["id"] == "calendar")["ready"] is True
    assert "calendar-refresh" not in json.dumps(report)


def test_replace_read_access_returns_to_the_consent_instructions(tmp_path, capsys):
    from ajss.onboard.checklist import parse_env_file
    upsert_env(tmp_path / ".env", {
        "GOOGLE_CLIENT_ID": "client-public",
        "GOOGLE_CLIENT_SECRET": "hidden-secret",
        "GOOGLE_GMAIL_REFRESH_TOKEN": "gmail-refresh",
        "GOOGLE_CALENDAR_REFRESH_TOKEN": "calendar-refresh",
    })
    code = run_onboard(
        Namespace(root=str(tmp_path), set=["replace=calendar"], listen=False, check=False, stage="calendar"),
    )
    output = capsys.readouterr().out
    assert code == 0
    report = json.loads(output)
    stored = parse_env_file(tmp_path / ".env")
    assert "GOOGLE_CALENDAR_REFRESH_TOKEN" not in stored
    assert stored["GOOGLE_GMAIL_REFRESH_TOKEN"] == "gmail-refresh"
    assert report["next"]["id"] == "calendar"
    assert report["next"]["mode"] == "retrieve"
    assert "calendar-refresh" not in output
    assert "gmail-refresh" not in output


def test_stage_database_checks_tables_without_the_opening_question(tmp_path, monkeypatch, capsys):
    for name in ("DATABASE_URL", "AJSS_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    upsert_env(tmp_path / ".env", {"DATABASE_URL": DATABASE})
    seen = []

    def schema(url):
        seen.append(url)
        return False

    code = run_onboard(
        Namespace(root=str(tmp_path), set=[], listen=False, check=False, stage="database"),
        schema=schema,
    )
    output = capsys.readouterr().out
    assert code == 0
    assert seen == [DATABASE]
    report = json.loads(output)
    assert report["gate"] == "run"
    assert report["next"]["id"] == "database"
    assert report["next"]["mode"] == "confirm"
    assert report["opening"] == ""
    assert "The queue tables are already there." in report["next"]["instructions"]
    assert "planted-password" not in output
    assert "Do you want to proceed" not in output
