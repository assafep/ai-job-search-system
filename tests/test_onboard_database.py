"""The database step asks where the URL comes from, then creates missing queue tables."""
import json

import pytest
from argparse import Namespace

from ajss.onboard.checklist import apply_settings, assess, read_state, run_onboard, upsert_env, write_state
from ajss.onboard.database import QUEUE_DDL, DatabaseCheckError, ensure_queue_tables, is_postgres_url
from ajss.settings import database_url

from tests.test_local_onboarding import DATABASE, DRIVE, JOURNAL


OLD = "postgres://app:old-secret@db.example/ajss"
NEW = "postgres://app:new-secret@db.example/ajss"
LEAKED = (
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
    "GOOGLE_GMAIL_REFRESH_TOKEN", "GOOGLE_CALENDAR_REFRESH_TOKEN",
    "GOOGLE_ACCESS_TOKEN", "GOOGLE_SERVICE_ACCOUNT_JSON",
    "DATABASE_URL", "AJSS_DATABASE_URL", "AJSS_ALLOW_LOCAL_SQLITE",
    "GOOGLE_RESUME_DRIVE_PATH", "AJSS_RESUME_DRIVE_PATH", "OPENAI_API_KEY", "XAI_API_KEY",
    "AJSS_LLM_API_KEY", "AJSS_LLM_PROVIDER", "AJSS_LLM_MODEL",
)


def _quiet(monkeypatch):
    for name in LEAKED:
        monkeypatch.delenv(name, raising=False)


def _ready_through_journal(tmp_path):
    runtime = tmp_path / "config" / "runtime.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({"google_drive": {
        "interview_journal_url": JOURNAL,
        "artifact_root_url": DRIVE,
    }}), encoding="utf-8")
    write_state(tmp_path, {"journal_confirmed": True})
    upsert_env(tmp_path / ".env", {
        "GOOGLE_CLIENT_ID": "client-public",
        "GOOGLE_CLIENT_SECRET": "hidden-secret",
        "GOOGLE_REFRESH_TOKEN": "refresh-secret",
    })


def _run(tmp_path, assignments, schema, resources=None):
    if resources is None:
        resources = {}
    code = run_onboard(
        Namespace(root=str(tmp_path), set=list(assignments), listen=False, check=False),
        resources=resources,
        sanity=lambda *args, **kwargs: {"ok": True, "checks": []},
        schema=schema,
    )
    return code, resources


class _Cursor:
    def __init__(self, present, log):
        self.present = present
        self.log = log
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.log.append(sql)
        if "information_schema" in sql:
            self.rows = [(name,) for name in sorted(self.present)]
        elif "CREATE TABLE IF NOT EXISTS public." in sql:
            name = sql.split("public.", 1)[1].split("(", 1)[0].strip()
            self.present.add(name)

    def fetchall(self):
        return list(self.rows)


class _Connection:
    def __init__(self, present, log):
        self.cursor_obj = _Cursor(present, log)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def close(self):
        self.closed = True


def test_queue_statements_do_not_copy_history():
    text = "\n".join(QUEUE_DDL).upper()
    assert "INSERT INTO" not in text
    assert "APPLICATIONS" not in text
    assert "DROP " not in text
    assert "RAISE " not in text
    assert "CREATE TABLE IF NOT EXISTS PUBLIC.GDTQ_CHECKPOINTS" in text
    assert "CREATE TABLE IF NOT EXISTS PUBLIC.POSTING_KEYS" in text


def test_missing_tables_are_created_and_existing_tables_are_left_alone():
    log = []
    present = set()
    connection = _Connection(present, log)
    created = ensure_queue_tables(OLD, connect=lambda: connection)
    assert created is True
    assert connection.closed is True
    assert sum("CREATE TABLE" in sql for sql in log) == 5
    assert all("applications" not in sql for sql in log)
    assert sum("posting_keys" in sql for sql in log) == 1
    log.clear()
    again = ensure_queue_tables(OLD, connect=lambda: _Connection(present, log))
    assert again is False
    assert log == [
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = ANY(%s)"
    ]


def test_a_connection_error_does_not_keep_the_url():
    url = OLD

    def connect():
        raise RuntimeError(url)

    with pytest.raises(DatabaseCheckError) as caught:
        ensure_queue_tables(url, connect=connect)
    assert str(caught.value) == "The database did not answer."
    assert caught.value.__cause__ is None
    assert url not in str(caught.value)


def test_only_a_postgres_url_counts():
    assert is_postgres_url(DATABASE) is True
    assert is_postgres_url("postgresql://db.example/ajss") is True
    assert is_postgres_url("http://db.example/ajss") is False
    assert is_postgres_url("postgres://") is False
    assert database_url({"AJSS_DATABASE_URL": OLD}) == OLD
    assert database_url({"DATABASE_URL": DATABASE, "AJSS_DATABASE_URL": OLD}) == DATABASE


def test_a_missing_url_asks_for_an_existing_database_or_vercel(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _ready_through_journal(tmp_path)
    called = []
    code, _resources = _run(
        tmp_path,
        ["wizard=yes", "use=google", "use=drive_root", "use=journal", "use=journal_confirmed"],
        lambda url: called.append(url),
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert called == []
    assert report["next"]["id"] == "database"
    assert report["next"]["mode"] == "retrieve"
    assert "already started" in report["next"]["instructions"]
    assert "Vercel" in report["next"]["instructions"]
    assert "https://vercel.com/marketplace/neon/neon" in json.dumps(report["next"]["links"])
    text = json.dumps(report)
    assert "--set" not in report["next"]["instructions"]
    assert OLD not in text


def test_vercel_and_existing_are_separate_guides(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _ready_through_journal(tmp_path)
    prior = ["wizard=yes", "use=google", "use=drive_root", "use=journal", "use=journal_confirmed"]
    code, _resources = _run(tmp_path, prior + ["database_choice=vercel"], lambda url: None)
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert "Create New Neon Account" in report["next"]["instructions"]
    assert "/tmp/ajss-vercel.env" in report["next"]["instructions"]
    assert "https://vercel.com/dashboard" in json.dumps(report["next"]["links"])
    code, _resources = _run(tmp_path, ["database_choice=existing"], lambda url: None)
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert "AJSS_DATABASE_URL" in report["next"]["instructions"]
    assert "Do not paste" in report["next"]["instructions"]
    with pytest.raises(ValueError, match="existing or vercel"):
        apply_settings(
            [("database_choice", "later")],
            tmp_path, tmp_path / "config" / "runtime.json", tmp_path / ".env",
        )


def test_an_established_url_creates_missing_tables_without_printing_it(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _ready_through_journal(tmp_path)
    upsert_env(tmp_path / ".env", {"AJSS_DATABASE_URL": OLD})
    seen = []

    def schema(url):
        seen.append(url)
        return True

    code, resources = _run(
        tmp_path,
        ["wizard=yes", "use=google", "use=drive_root", "use=journal", "use=journal_confirmed"],
        schema,
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert seen == [OLD]
    assert report["next"]["mode"] == "confirm"
    assert report["next"]["instructions"].startswith("The queue tables were created.")
    assert "Use this database" in report["next"]["instructions"]
    output = json.dumps(report)
    assert OLD not in output
    assert "old-secret" not in output
    state_text = (tmp_path / "var" / "onboarding-state.json").read_text(encoding="utf-8")
    assert "old-secret" not in state_text

    def refuse(url):
        raise AssertionError(url)

    code, _resources = _run(tmp_path, [], refuse, resources)
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["next"]["instructions"].startswith("The queue tables were created.")


def test_tables_already_there_are_reported(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _ready_through_journal(tmp_path)
    upsert_env(tmp_path / ".env", {"DATABASE_URL": DATABASE, "AJSS_DATABASE_URL": OLD})
    seen = []
    code, _resources = _run(
        tmp_path,
        ["wizard=yes", "use=google", "use=drive_root", "use=journal", "use=journal_confirmed"],
        lambda url: seen.append(url) or False,
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert seen == [DATABASE]
    assert report["next"]["instructions"].startswith("The queue tables are already there.")
    assert DATABASE not in json.dumps(report)
    assert "planted-password" not in json.dumps(report)


def test_the_database_is_not_contacted_before_that_step(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _ready_through_journal(tmp_path)
    upsert_env(tmp_path / ".env", {"DATABASE_URL": DATABASE})

    def schema(url):
        raise AssertionError(url)

    code, _resources = _run(tmp_path, ["wizard=yes"], schema)
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["next"]["id"] == "google"
    assert DATABASE not in json.dumps(report)


def test_an_unreachable_database_stays_on_the_step(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _ready_through_journal(tmp_path)
    upsert_env(tmp_path / ".env", {"DATABASE_URL": OLD})

    def schema(url):
        raise RuntimeError(url)

    code, _resources = _run(
        tmp_path,
        ["wizard=yes", "use=google", "use=drive_root", "use=journal", "use=journal_confirmed"],
        schema,
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["next"]["id"] == "database"
    assert report["next"]["mode"] == "retrieve"
    assert "did not answer" in report["next"]["instructions"]
    text = json.dumps(report)
    assert OLD not in text
    assert "old-secret" not in text
    stored = (tmp_path / "var" / "onboarding-state.json").read_text(encoding="utf-8")
    assert "old-secret" not in stored
    assert read_state(tmp_path)["database_check"] == "failed"


def test_replacing_the_database_asks_again_and_waits_for_a_new_url(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _ready_through_journal(tmp_path)
    upsert_env(tmp_path / ".env", {"DATABASE_URL": OLD})
    seen = []
    prior = ["wizard=yes", "use=google", "use=drive_root", "use=journal", "use=journal_confirmed"]
    code, _resources = _run(tmp_path, prior + ["replace=database"], lambda url: seen.append(url))
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert seen == []
    assert report["next"]["id"] == "database"
    assert report["next"]["mode"] == "retrieve"
    assert "Vercel" in report["next"]["instructions"]
    assert OLD not in json.dumps(report)

    code, _resources = _run(tmp_path, ["database_choice=vercel"], lambda url: seen.append(url))
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert seen == []
    assert "Create New Neon Account" in report["next"]["instructions"]

    upsert_env(tmp_path / ".env", {"DATABASE_URL": NEW})
    code, _resources = _run(tmp_path, [], lambda url: seen.append(url) or True)
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert seen == [NEW]
    assert report["next"]["instructions"].startswith("The queue tables were created.")
    assert NEW not in json.dumps(report)
    assert "new-secret" not in json.dumps(report)


def test_choosing_the_stored_url_checks_it(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _ready_through_journal(tmp_path)
    upsert_env(tmp_path / ".env", {"AJSS_DATABASE_URL": OLD})
    seen = []
    prior = ["wizard=yes", "use=google", "use=drive_root", "use=journal", "use=journal_confirmed"]
    _run(tmp_path, prior + ["replace=database"], lambda url: seen.append(url))
    capsys.readouterr()
    code, _resources = _run(tmp_path, ["database_choice=existing"], lambda url: seen.append(url) or False)
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert seen == [OLD]
    assert report["next"]["instructions"].startswith("The queue tables are already there.")
    assert "old-secret" not in json.dumps(report)


def test_local_sqlite_does_not_open_postgres(tmp_path, monkeypatch, capsys):
    _quiet(monkeypatch)
    _ready_through_journal(tmp_path)
    code, _resources = _run(
        tmp_path,
        [
            "wizard=yes", "use=google", "use=drive_root", "use=journal", "use=journal_confirmed",
            "database_mode=local",
        ],
        lambda url: (_ for _ in ()).throw(AssertionError(url)),
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["next"]["id"] != "database"
    report = assess(
        {
            "GOOGLE_CLIENT_ID": "client-public",
            "GOOGLE_CLIENT_SECRET": "hidden-secret",
            "GOOGLE_REFRESH_TOKEN": "refresh-secret",
            "AJSS_ALLOW_LOCAL_SQLITE": "1",
        },
        tmp_path,
        tmp_path / "config" / "runtime.json",
    )
    database = next(step for step in report["steps"] if step["id"] == "database")
    assert database["ready"] is True


def test_a_url_that_is_not_postgres_is_not_opened(tmp_path):
    report = assess({"DATABASE_URL": "not-a-database"}, tmp_path, tmp_path / "runtime.json")
    database = next(step for step in report["steps"] if step["id"] == "database")
    assert database["ready"] is False
    assert "postgres://" in database["instructions"]
    assert "not-a-database" not in json.dumps(report)
