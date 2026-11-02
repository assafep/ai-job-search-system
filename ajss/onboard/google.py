"""Idempotent Google Drive and main queue document bootstrap.

This module is safe to unit test with injected clients. Its CLI performs live
Google writes only when an operator explicitly runs ``python -m ajss.onboard.google``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from adapters.google.drive import GOOGLE_DOC_MIME, GoogleDriveArtifactStore

DEFAULT_QUEUE_TITLE = "AJSS Main Queue"
SOFT_TAB_LIMIT = 90
WORKER_QUEUES = (
    "Discovered", "Bookmarked", "Validated", "Prepared", "Applied",
    "Interviewing", "Manual Apply Required", "Dead Letters Queue",
)
DOC_ID_RE = re.compile(r"/document/(?:u/\d+/)?d/([^/?]+)")


def document_id(value: str) -> str:
    match = DOC_ID_RE.search(value or "")
    if not match:
        raise ValueError("Queue document URL must be a Google Docs URL")
    return match.group(1)


def bootstrap_google_workspace(
    access_token,
    drive_root_url,
    *,
    queue_document_url=None,
    queue_document_title=DEFAULT_QUEUE_TITLE,
    drive_factory=GoogleDriveArtifactStore,
    docs_factory=None,
    queue_factory=None,
):
    """Connect to the selected Drive folder and provision the main queue doc.

    A supplied document URL must identify a native Google Doc directly in the
    selected Drive folder. Without a URL, an exact-title document is reused;
    multiple matches are treated as ambiguous. The standard Applications
    subdirectory and missing queue tabs are created idempotently.
    """
    if docs_factory is None:
        from adapters.google.docs import GoogleDocsClient
        docs_factory = GoogleDocsClient
    if queue_factory is None:
        from ajss.queue import GDTQ
        queue_factory = GDTQ
    from ajss.queue.policy import StateTransitionEngine
    from ajss.queue import QueueFull, prop

    drive = drive_factory(access_token, drive_root_url)
    files = drive.list_root_files()
    if queue_document_url:
        selected_id = document_id(queue_document_url)
        matches = [item for item in files if item.get("id") == selected_id]
        if len(matches) != 1 or matches[0].get("mimeType") != GOOGLE_DOC_MIME:
            raise ValueError("Selected queue document is not a Google Doc in the configured Drive directory")
        selected = matches[0]
    else:
        matches = [item for item in files
                   if item.get("name") == queue_document_title
                   and item.get("mimeType") == GOOGLE_DOC_MIME]
        if len(matches) > 1:
            raise ValueError("Multiple main queue documents match the configured title")
        selected = matches[0] if matches else drive.create_google_doc(queue_document_title)
        selected_id = selected.get("id")
        if not selected_id:
            raise RuntimeError("Drive did not return an ID for the main queue document")

    client = docs_factory(access_token, selected_id)
    queue = queue_factory(client, max_tabs=100)
    state_tabs = list(StateTransitionEngine().transitions)
    state_tabs.extend(state + "-onFlight" for state in WORKER_QUEUES)
    snapshot = queue.snapshot()
    root_titles = {
        prop(tab, "title") for tab in snapshot.get("tabs", [])
        if not prop(tab, "parentTabId")
    }
    missing = [name for name in state_tabs if name not in root_titles]
    if queue.total_tabs(snapshot) + len(missing) > SOFT_TAB_LIMIT:
        raise QueueFull(
            "Queue bootstrap would exceed the {}-tab soft limit; current={}, missing={}".format(
                SOFT_TAB_LIMIT, queue.total_tabs(snapshot), len(missing)))
    drive.ensure_application_directory()
    created_tabs = queue.ensure_state_tabs(state_tabs)
    return {
        "document_id": selected_id,
        "document_url": "https://docs.google.com/document/d/{}/edit".format(selected_id),
        "document_title": selected.get("name") or queue_document_title,
        "drive_root_url": drive_root_url,
        "created_tabs": created_tabs,
    }


def write_runtime_config(result, path):
    """Write the runtime's supported config shape using an atomic replacement."""
    target = Path(path)
    if target.exists() and target.is_dir():
        raise IsADirectoryError(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    config = {}
    if target.is_file():
        config = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("Runtime config must contain a JSON object")
    config["google_drive"] = {
        "interview_journal_url": result["document_url"],
        "artifact_root_url": result["drive_root_url"],
    }
    fd, temporary = tempfile.mkstemp(prefix=".runtime-", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="Connect AJSS to Google Drive and provision its main queue")
    parser.add_argument("--drive-root-url", required=True)
    parser.add_argument("--queue-document-url")
    parser.add_argument("--queue-document-title", default=DEFAULT_QUEUE_TITLE)
    parser.add_argument("--config", default="config/runtime.json")
    args = parser.parse_args(argv)
    if Path(args.config).exists() and Path(args.config).is_dir():
        raise IsADirectoryError(args.config)

    from adapters.google.auth import token_from_environment
    token = token_from_environment()
    if token is None:
        raise RuntimeError("Google OAuth credentials are not configured")
    result = bootstrap_google_workspace(
        token, args.drive_root_url,
        queue_document_url=args.queue_document_url,
        queue_document_title=args.queue_document_title,
    )
    path = write_runtime_config(result, args.config)
    print(json.dumps({**result, "config_path": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
