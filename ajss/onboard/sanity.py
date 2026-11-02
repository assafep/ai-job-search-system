"""Check that a completed local setup is valid and working.

The wizard runs this once every required value is already stored. Each check
reports a fixed sentence. Connection errors, keys, and contact details stay out
of the result.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path


class CheckFailure(Exception):
    """A check failed with a sentence that is safe to show."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


CHECKS = (
    ("google", "Google account", "google", "Google did not accept the stored account."),
    ("drive_root", "Drive folder", "drive_folder", "The Drive folder could not be opened."),
    ("journal", "Interviews journal", "journal", "The interviews journal could not be opened."),
    ("database", "Processed-applications database", "database", "The database did not answer."),
    ("resume_path", "Résumé", "resume", "The résumé PDF was not found in Drive."),
    ("llm_key", "Model API", "model", "The model API did not answer."),
    ("profile", "Applicant profile", "profile", "The applicant profile could not be read."),
    ("calendly", "Calendly link", "calendly", "The Calendly link did not answer."),
)

_AFTER_GOOGLE = {"drive_root", "journal", "resume_path", "profile"}
_AFTER_DRIVE = {"resume_path", "profile"}
_DOC_ID = re.compile(r"/document/(?:u/\d+/)?d/([^/?#]+)")


def profile_status(document):
    """Readable profile with the required contacts. The sentence omits the values."""
    from ajss.onboard.profile import REQUIRED_CONTACTS, contact_text
    if not isinstance(document, dict):
        raise CheckFailure("The applicant profile could not be read.")
    personal = document.get("personal") if isinstance(document.get("personal"), dict) else {}
    for item in REQUIRED_CONTACTS:
        if not contact_text(personal, item["field"]):
            raise CheckFailure(
                "The applicant profile is missing " + item["article"] + " " + item["label"] + "."
            )
    return "The applicant profile is readable and includes the required contact details."


class LiveChecks:
    """Network checks for the stored Google account, database, and model API."""

    def __init__(self, env, root, runtime_path, resources=None):
        self.env = env
        self.root = Path(root)
        self.runtime_path = runtime_path
        self.resources = resources if resources is not None else {}
        self._store = None

    def store(self):
        if self._store is not None:
            return self._store
        from ajss.onboard.checklist import _drive_store
        previous = dict(os.environ)
        os.environ.update({key: value for key, value in self.env.items() if value})
        try:
            self._store = _drive_store(self.env, self.runtime_path, self.resources)
        finally:
            os.environ.clear()
            os.environ.update(previous)
        return self._store

    def google(self):
        from adapters.google.auth import access_token_value
        token = access_token_value(self.store().access_token)
        if not token:
            raise CheckFailure("Google did not accept the stored account.")
        return "Google accepted the stored account."

    def drive_folder(self):
        from adapters.google.drive import FOLDER_MIME
        store = self.store()
        meta = store.stat_id(store.root_folder_id)
        if meta.get("mimeType") != FOLDER_MIME:
            raise CheckFailure("The Drive folder could not be opened.")
        return "The Drive folder is reachable."

    def journal(self):
        from adapters.google.drive import GOOGLE_DOC_MIME
        from ajss.onboard.checklist import _read_runtime
        config = _read_runtime(self.runtime_path)
        url = str((config.get("google_drive") or {}).get("interview_journal_url") or "")
        match = _DOC_ID.search(url)
        if not match:
            raise CheckFailure("The interviews journal could not be opened.")
        meta = self.store().stat_id(match.group(1))
        if meta.get("mimeType") != GOOGLE_DOC_MIME:
            raise CheckFailure("The interviews journal could not be opened.")
        return "The interviews journal is reachable."

    def database(self):
        from ajss.settings import database_url
        from ajss.queue.schema import gaps_sentence, schema_gaps, sqlite_gaps
        url = database_url(self.env)
        if url:
            import psycopg
            with psycopg.connect(url, connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    gaps = schema_gaps(cur)
            if gaps:
                raise CheckFailure(gaps_sentence(gaps))
            return "The database has the queue tables."
        if self.env.get("AJSS_ALLOW_LOCAL_SQLITE") == "1":
            path = self.root / "var" / "ajss-gdtq.sqlite3"
            if not path.is_file():
                return "A local SQLite database is selected."
            import sqlite3
            conn = sqlite3.connect("file:{}?mode=ro".format(path), uri=True)
            try:
                gaps = sqlite_gaps(conn)
            finally:
                conn.close()
            if gaps:
                raise CheckFailure(gaps_sentence(gaps, local=True))
            return "The local database has the queue tables."
        raise CheckFailure("The database did not answer.")

    def resume(self):
        from ajss.settings import resume_drive_path
        path = resume_drive_path(self.env)
        if not path:
            raise CheckFailure("The résumé PDF was not found in Drive.")
        meta = self.store().stat_path(path)
        name = str(meta.get("name") or path).lower()
        if meta.get("mimeType") != "application/pdf" and not name.endswith(".pdf"):
            raise CheckFailure("The résumé PDF was not found in Drive.")
        return "The résumé PDF is in Drive."

    def model(self):
        from adapters.llm.providers import selection
        selected = selection(self.env)
        if not selected.base_url:
            raise CheckFailure("The model API did not answer.")
        url = selected.base_url.rstrip("/") + "/models"
        request = urllib.request.Request(url, method="GET")
        if selected.api_key:
            request.add_header("Authorization", "Bearer " + selected.api_key)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                response.read(64)
        except urllib.error.HTTPError as exc:
            raise CheckFailure("The model API did not answer.") from exc
        if selected.api_key:
            return "The model API accepted the key."
        return "The model server answered."

    def profile(self):
        from ajss.onboard.profile import PROFILE_PATH
        found = self.store().read_path(PROFILE_PATH)
        content = found["content"]
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        try:
            document = json.loads(content)
        except json.JSONDecodeError as exc:
            raise CheckFailure("The applicant profile could not be read.") from exc
        return profile_status(document)

    def calendly(self):
        from ajss.onboard.checklist import _confirmed, read_state, _state_path
        from ajss.onboard.profile import validate_calendly_url
        state = read_state(self.root) if _state_path(self.root).is_file() else {}
        if _confirmed(state, "calendly_declined"):
            return "No Calendly link will be added to cover letters."
        url = str(state.get("calendly_on_profile") or state.get("calendly_url") or "").strip()
        if not url:
            raise CheckFailure("The Calendly link did not answer.")
        validate_calendly_url(url)
        self._ping(url)
        return "The Calendly link answered."

    def _ping(self, url):
        request = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            status = exc.code
        if status == 405:
            fallback = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(fallback, timeout=10) as response:
                    status = getattr(response, "status", 200)
                    response.read(64)
            except urllib.error.HTTPError as exc:
                status = exc.code
        if status == 404 or status >= 500:
            raise CheckFailure("The Calendly link did not answer.")


def run_sanity(env, root, runtime_path, resources=None, client=None):
    """Run every check. A failed Google or Drive check skips the checks that need it."""
    checker = client or LiveChecks(env, root, runtime_path, resources)
    checks = []
    failed = set()
    for check_id, label, method, fallback in CHECKS:
        if check_id in _AFTER_GOOGLE and "google" in failed:
            continue
        if check_id in _AFTER_DRIVE and "drive_root" in failed:
            continue
        try:
            detail = getattr(checker, method)()
        except CheckFailure as exc:
            detail = exc.detail
            ok = False
        except Exception:
            detail = fallback
            ok = False
        else:
            ok = True
        checks.append({"id": check_id, "label": label, "ok": ok, "detail": detail})
        if not ok:
            failed.add(check_id)
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def main(argv=None):
    from ajss.onboard.checklist import merged_environment, project_root
    root = project_root()
    env = merged_environment(root / ".env")
    result = run_sanity(env, root, root / "config" / "runtime.json", resources={})
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 2
