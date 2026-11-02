"""The wizard names stored values before collecting, and checks a complete setup."""
import json

import pytest
from argparse import Namespace

from ajss.onboard.checklist import apply_settings, run_onboard, upsert_env, write_state
from ajss.onboard.sanity import CheckFailure, profile_status, run_sanity

from tests.test_local_onboarding import DATABASE, DRIVE, JOURNAL, SECRET


LEAKED = (
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
    "GOOGLE_GMAIL_REFRESH_TOKEN", "GOOGLE_CALENDAR_REFRESH_TOKEN",
    "GOOGLE_ACCESS_TOKEN", "GOOGLE_SERVICE_ACCOUNT_JSON",
    "DATABASE_URL", "AJSS_DATABASE_URL", "AJSS_ALLOW_LOCAL_SQLITE",
    "GOOGLE_RESUME_DRIVE_PATH", "AJSS_RESUME_DRIVE_PATH", "OPENAI_API_KEY", "XAI_API_KEY",
    "AJSS_LLM_API_KEY", "AJSS_LLM_PROVIDER", "AJSS_LLM_MODEL",
    "AJSS_CONTACT_EMAIL", "AJSS_CONTACT_PHONE",
)
PHONE = "+1-555-0109"


def _quiet(monkeypatch):
    for name in LEAKED:
        monkeypatch.delenv(name, raising=False)


def _runtime(tmp_path, folder=True):
    runtime = tmp_path / "config" / "runtime.json"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    drive = {}
    if folder:
        drive = {"interview_journal_url": JOURNAL, "artifact_root_url": DRIVE}
    runtime.write_text(json.dumps({"google_drive": drive}), encoding="utf-8")
    return runtime


def _google_env(tmp_path):
    upsert_env(tmp_path / ".env", {
        "GOOGLE_CLIENT_ID": "client-public",
        "GOOGLE_CLIENT_SECRET": "hidden-secret",
        "GOOGLE_REFRESH_TOKEN": "refresh-secret",
    })


def _complete_env(tmp_path):
    _google_env(tmp_path)
    upsert_env(tmp_path / ".env", {
        "DATABASE_URL": DATABASE,
        "GOOGLE_RESUME_DRIVE_PATH": "Applicant/resume.pdf",
        "OPENAI_API_KEY": SECRET,
        "GOOGLE_GMAIL_REFRESH_TOKEN": "gmail-refresh-secret",
        "GOOGLE_CALENDAR_REFRESH_TOKEN": "calendar-refresh-secret",
    })
    write_state(tmp_path, {
        "journal_confirmed": True,
        "resume_in_drive": True,
        "profile_sha256": "a" * 64,
        "profile_missing": [],
        "calendly_declined": True,
        "contacts": {"phone": PHONE, "email": "ada@example.test"},
    })


def _report(tmp_path, assignments=(), sanity=None, resources=None):
    if sanity is None:
        sanity = lambda *args, **kwargs: {"ok": True, "checks": []}
    if resources is None:
        resources = {}
    code = run_onboard(
        Namespace(root=str(tmp_path), set=list(assignments), listen=False, check=False),
        resources=resources,
        sanity=sanity,
    )
    return code


def _printed(capsys):
    return json.loads(capsys.readouterr().out)


def test_profile_status_does_not_repeat_contact_details():
    detail = profile_status({"personal": {"email": "ada@example.test", "phone": PHONE}})
    assert PHONE not in detail
    assert "ada@example.test" not in detail
    assert "required contact details" in detail
    with pytest.raises(CheckFailure, match="phone number"):
        profile_status({"personal": {"email": "ada@example.test"}})


def test_database_check_names_a_missing_checkpoint_table(tmp_path, monkeypatch):
    from ajss.onboard.sanity import LiveChecks
    from ajss.queue.schema import REQUIRED_COLUMNS

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            return None

        def fetchall(self):
            rows = []
            for table, columns in REQUIRED_COLUMNS.items():
                if table == "gdtq_checkpoints":
                    continue
                for column in columns:
                    rows.append((table, column))
            return rows

    class Connection:
        def cursor(self):
            return Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    checker = LiveChecks(
        {"DATABASE_URL": "postgres://app:planted-password@db.example/ajss"},
        tmp_path,
        tmp_path / "runtime.json",
    )
    with pytest.raises(CheckFailure) as caught:
        checker.database()
    assert str(caught.value) == "The database is missing gdtq_checkpoints."
    assert "planted-password" not in str(caught.value)


def test_database_check_accepts_the_queue_tables(tmp_path, monkeypatch):
    from ajss.onboard.sanity import LiveChecks
    from ajss.queue.schema import REQUIRED_COLUMNS

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            return None

        def fetchall(self):
            return [
                (table, column)
                for table, columns in REQUIRED_COLUMNS.items()
                for column in columns
            ]

    class Connection:
        def cursor(self):
            return Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    checker = LiveChecks(
        {"DATABASE_URL": "postgres://app:planted-password@db.example/ajss"},
        tmp_path,
        tmp_path / "runtime.json",
    )
    assert checker.database() == "The database has the queue tables."


def test_local_database_check_names_missing_queue_tables(tmp_path):
    import sqlite3
    from ajss.onboard.sanity import LiveChecks
    path = tmp_path / "var" / "ajss-gdtq.sqlite3"
    path.parent.mkdir()
    sqlite3.connect(path).close()
    checker = LiveChecks({"AJSS_ALLOW_LOCAL_SQLITE": "1"}, tmp_path, tmp_path / "runtime.json")
    with pytest.raises(CheckFailure) as caught:
        checker.database()
    assert str(caught.value) == "The local database is missing identities, items, checkpoints, event_log."
    from ajss.queue.store import WorkIndex
    index = WorkIndex(path)
    index.close()
    assert checker.database() == "The local database has the queue tables."


def test_a_failed_google_check_skips_drive_and_hides_the_error():
    class Client:
        def google(self):
            raise RuntimeError("refresh-secret")

        def database(self):
            raise RuntimeError("postgres://app:planted-password@db/ajss")

        def model(self):
            return "The model API accepted the key."

        def calendly(self):
            return "No Calendly link will be added to cover letters."

    result = run_sanity({}, None, None, client=Client())
    text = json.dumps(result)
    assert result["ok"] is False
    assert "refresh-secret" not in text
    assert "planted-password" not in text
    ids = [item["id"] for item in result["checks"]]
    assert "google" in ids
    assert "drive_root" not in ids
    assert "resume_path" not in ids
    assert "database" in ids
    assert "The database did not answer." in text
    assert "The model API accepted the key." in text


def test_empty_setup_starts_at_the_first_question(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    code = _report(tmp_path)
    report = _printed(capsys)
    assert code == 0
    assert report["gate"] == "run"
    assert report["opening"] == ""
    assert report["present"] == []
    assert report["next"]["id"] == "google"
    assert report["next"]["mode"] == "retrieve"
    assert report["can_start"] is False


def test_partial_setup_lists_stored_values_and_asks_to_proceed(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _runtime(tmp_path)
    _google_env(tmp_path)
    calls = []

    def runner(*args, **kwargs):
        calls.append(1)
        return {"ok": True, "checks": []}

    code = _report(tmp_path, sanity=runner)
    report = _printed(capsys)
    assert code == 0
    assert calls == []
    assert report["gate"] == "ask"
    assert report["sanity"] is None
    assert report["can_start"] is False
    assert report["next"]["mode"] == "ask"
    assert "These values are already set." in report["opening"]
    assert DRIVE in report["opening"]
    assert JOURNAL in report["opening"]
    assert "a refresh token is stored" in report["opening"]
    assert "These are still missing:" in report["opening"]
    assert "Do you want to proceed with the wizard?" in report["opening"]
    assert "The checks passed" not in report["opening"]
    assert "hidden-secret" not in json.dumps(report)


def test_complete_setup_runs_the_checks_and_asks_before_the_wizard(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _runtime(tmp_path)
    _complete_env(tmp_path)
    calls = []

    def runner(*args, **kwargs):
        calls.append("run")
        return {
            "ok": True,
            "checks": [{
                "id": "google",
                "label": "Google account",
                "ok": True,
                "detail": "Google accepted the stored account.",
            }],
        }

    resources = {}
    code = _report(tmp_path, sanity=runner, resources=resources)
    report = _printed(capsys)
    assert code == 0
    assert calls == ["run"]
    assert report["ready"] is True
    assert report["gate"] == "ask"
    assert report["can_start"] is False
    assert "It seems everything required is already set." in report["opening"]
    assert "The checks passed." in report["opening"]
    assert "Google accepted the stored account." in report["opening"]
    assert "Do you still want to run the wizard?" in report["opening"]
    assert DRIVE in report["opening"]
    text = json.dumps(report)
    assert SECRET not in text
    assert "planted-password" not in text
    assert "hidden-secret" not in text
    assert "gmail-refresh-secret" not in text
    assert "calendar-refresh-secret" not in text
    assert PHONE not in text
    assert "ada@example.test" not in text
    code = _report(tmp_path, sanity=runner, resources=resources)
    assert code == 0
    assert calls == ["run"]
    _printed(capsys)


def test_failed_checks_are_named_and_a_raised_error_is_not_copied(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _runtime(tmp_path)
    _complete_env(tmp_path)

    def runner(*args, **kwargs):
        raise RuntimeError("postgres://app:planted-password@db/ajss " + SECRET)

    code = _report(tmp_path, sanity=runner)
    report = _printed(capsys)
    assert code == 0
    assert report["gate"] == "ask"
    assert report["can_start"] is False
    assert "These checks did not pass." in report["opening"]
    assert "The checks could not be run." in report["opening"]
    assert "Do you want to run the wizard to correct them?" in report["opening"]
    assert "The checks passed" not in report["opening"]
    text = json.dumps(report)
    assert "planted-password" not in text
    assert SECRET not in text


def test_declining_the_wizard_leaves_the_stored_values(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _runtime(tmp_path)
    _complete_env(tmp_path)
    code = _report(tmp_path, ["wizard=no"])
    report = _printed(capsys)
    assert code == 0
    assert report["gate"] == "stop"
    assert report["next"] is None
    assert report["can_start"] is True
    assert report["ready"] is True
    assert "leave the stored values" in report["opening"]
    assert "The checks passed." in report["opening"]


def test_each_stored_step_asks_to_use_or_replace_it(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _runtime(tmp_path)
    _google_env(tmp_path)
    code = _report(tmp_path, ["wizard=yes"])
    report = _printed(capsys)
    assert code == 0
    assert report["gate"] == "run"
    assert report["next"]["id"] == "google"
    assert report["next"]["mode"] == "confirm"
    assert "refresh token" in report["next"]["instructions"]
    assert "Desktop app" not in report["next"]["instructions"]

    code = _report(tmp_path, ["use=google"])
    report = _printed(capsys)
    assert code == 0
    assert report["next"]["id"] == "drive_root"
    assert report["next"]["mode"] == "confirm"
    assert DRIVE in report["next"]["instructions"]
    assert "Use this value, or replace it?" in report["next"]["instructions"]

    code = _report(tmp_path, ["replace=drive_root"])
    report = _printed(capsys)
    assert code == 0
    assert report["next"]["id"] == "drive_root"
    assert report["next"]["mode"] == "retrieve"
    assert "address bar" in report["next"]["instructions"]

    with pytest.raises(ValueError, match="not set yet"):
        _report(tmp_path, ["use=resume_path"])
    with pytest.raises(ValueError, match="yes or no"):
        apply_settings([("wizard", "maybe")], tmp_path, tmp_path / "config" / "runtime.json", tmp_path / ".env")
    with pytest.raises(ValueError, match="unknown setup step"):
        apply_settings([("use", "passport")], tmp_path, tmp_path / "config" / "runtime.json", tmp_path / ".env")


def test_a_saved_phone_is_confirmed_without_repeating_it(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _runtime(tmp_path)
    _complete_env(tmp_path)
    kept = [
        "use=google", "use=drive_root", "use=journal", "use=journal_confirmed",
        "use=database", "use=resume_path", "use=resume_in_drive",
        "use=llm_provider", "use=llm_model", "use=llm_key",
        "use=profile", "use=contact_email",
    ]
    code = _report(tmp_path, ["wizard=yes", *kept])
    report = _printed(capsys)
    assert code == 0
    assert report["next"]["id"] == "contact_phone"
    assert report["next"]["mode"] == "confirm"
    assert "phone number" in report["next"]["instructions"]
    text = json.dumps(report)
    assert PHONE not in text
    assert "ada@example.test" not in text
