"""Prepare a local workspace for the deterministic integration tests.

A coding-agent login and a Google account are reported when they are already
visible. They are not required, and this command does not contact either one.
The pipeline tests mock the posting page and the form endpoint themselves.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def coding_agent_status(environ=None, which=shutil.which):
    environ = os.environ if environ is None else environ
    override = (environ.get("CODING_AGENT") or "").strip()
    names = [override] if override else ["grok", "codex"]
    found = [name for name in names if name and which(name)]
    if not found:
        return "not connected"
    return "present:" + ",".join(found)


def google_account_status(environ=None):
    environ = os.environ if environ is None else environ
    if (environ.get("GOOGLE_ACCESS_TOKEN") or "").strip():
        return "present"
    refresh = ("GOOGLE_REFRESH_TOKEN", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
    if all((environ.get(name) or "").strip() for name in refresh):
        return "present"
    if (environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip():
        return "present"
    return "not connected"


def prepare_workspace(root, environ=None):
    """Create the local index directory and report whether accounts are linked."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    database = root / "ajss-gdtq.sqlite3"
    from ajss.queue.store import WorkIndex
    index = WorkIndex(database)
    index.close()
    report = {
        "ready_for_integration_tests": True,
        "database": str(database),
        "google_account": google_account_status(environ),
        "coding_agent": coding_agent_status(environ),
        "tests": "python -m pytest -q tests/pipeline",
        "note": (
            "Deterministic pipeline tests mock job-board pages and form posts. "
            "They do not use the coding agent or Google."
        ),
    }
    path = root / "integration-workspace.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["workspace"] = str(path)
    return report


def main(argv=None):
    report = prepare_workspace(Path("var"))
    print(json.dumps(report, indent=2))
    return 0
