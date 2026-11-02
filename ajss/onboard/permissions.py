"""Read-only Gmail and Calendar grants for one OAuth client.

These stages run on their own. They store a separate refresh token and do not
replace the Drive token. The onboard process that already holds the Drive
client keeps that client.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from adapters.google_events import SCOPES

ENV_REFRESH = {
    "gmail": "GOOGLE_GMAIL_REFRESH_TOKEN",
    "calendar": "GOOGLE_CALENDAR_REFRESH_TOKEN",
}
TITLES = {
    "gmail": "Gmail read access",
    "calendar": "Calendar read access",
}
API_LINKS = {
    "gmail": (
        "Enable the Gmail API",
        "https://console.cloud.google.com/apis/library/gmail.googleapis.com",
    ),
    "calendar": (
        "Enable the Google Calendar API",
        "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com",
    ),
}
CONSENT_SCREEN = (
    "OAuth consent screen",
    "https://console.cloud.google.com/apis/credentials/consent",
)


def is_read_stage(stage):
    return stage in ENV_REFRESH


def file_path(root, kind):
    return Path(root) / ".onboarding" / (kind + "-user.json")


def granted(env, root, kind):
    """True when a read-only refresh token is stored. The token is not returned."""
    if (env.get(ENV_REFRESH[kind]) or "").strip():
        return True
    path = file_path(root, kind)
    if not path.is_file():
        return False
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(saved, dict):
        return False
    scope = str(saved.get("scope") or "")
    return SCOPES[kind] in scope.split() and bool(saved.get("refresh_token"))


def instructions(kind):
    if kind == "gmail":
        access = (
            "Allow read-only Gmail access for the Google account that owns the interviews journal. "
            "Enable the Gmail API in the same Cloud project. "
            "Open the OAuth consent screen, choose Data access, and add the Gmail scope "
            "that lets the app view your email messages and settings. "
            "AJSS uses this access to notice interview mail. This step does not send mail."
        )
        label = "Allow Gmail read access"
    else:
        access = (
            "Allow read-only Calendar access for the Google account that owns the interviews journal. "
            "Enable the Google Calendar API in the same Cloud project. "
            "Open the OAuth consent screen, choose Data access, and add the Calendar scope "
            "that lets the app view events on your calendars. "
            "AJSS uses this access to notice interview events. This step does not create or change events."
        )
        label = "Allow Calendar read access"
    text = (
        access + " "
        "Use the same Desktop app OAuth client named ajss-local. Do not create a second client. "
        "Leave the checkbox \"Use this client for an AI-powered agent\" unchecked. "
        "If the app is in testing, the Google account must be listed as a test user. "
        "Open the consent link and approve that read-only access. "
        "The redirect address is http://127.0.0.1:8765/ . A Desktop app often hides that field. "
        "Then run make onboard-listen ONBOARD_ARGS='--stage " + kind + "' "
        "and leave that command running until the browser answers. "
        "Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env when they are not there yet. "
        "Do not paste them into the chat."
    )
    return text, label


def clear_grant(root, env_path, kind):
    from ajss.onboard.checklist import upsert_env
    upsert_env(env_path, {}, remove=(ENV_REFRESH[kind],))
    path = file_path(root, kind)
    if path.is_file() or path.is_symlink():
        path.unlink()


def save_grant(root, env_path, kind, client_id, client_secret, refresh_token, scope):
    """Store the read token in .env and in the gitignored .onboarding file."""
    from ajss.onboard.checklist import upsert_env
    if SCOPES[kind] not in str(scope).split():
        raise RuntimeError(
            "Google did not grant read-only access. Open the consent link again and allow the requested access."
        )
    upsert_env(env_path, {ENV_REFRESH[kind]: refresh_token})
    path = file_path(root, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "scope": scope,
    }
    text = json.dumps(payload, indent=2) + "\n"
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.chmod(temporary, 0o600)
        try:
            os.replace(temporary, path)
        except OSError:
            path.write_text(text, encoding="utf-8")
    finally:
        if temporary.exists():
            temporary.unlink()
    os.chmod(path, 0o600)


def listen(kind, root, env_path, client_id, client_secret, opener, port=8765, timeout=300):
    from ajss.onboard.checklist import consent_url, exchange_grant, wait_for_code
    if not client_id or not client_secret:
        raise RuntimeError("Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env before --listen")
    redirect_uri = "http://127.0.0.1:{}/".format(port)
    url = consent_url(client_id, redirect_uri, scope=SCOPES[kind], include_granted=True)
    print(json.dumps({"open_this_url": url, "stage": kind}, indent=2), flush=True)
    code = wait_for_code(port, timeout)
    if not code:
        raise RuntimeError("No Google authorization code arrived. Add the redirect URI and open the link again")
    refresh_token, scope = exchange_grant(code, client_id, client_secret, redirect_uri, opener)
    save_grant(root, env_path, kind, client_id, client_secret, refresh_token, scope)
