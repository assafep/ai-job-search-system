"""Idempotent local-run checklist for a person using the Codex or Grok CLI.

The command inspects files and the environment, then reports the single next
step and clickable links. It does not prompt and it does not print secrets.
Re-running it after the same answers leaves the workspace unchanged.

When some values are already stored, the first report lists them and asks
whether to run the wizard. A complete setup is checked before that question.
During the wizard, a stored value is kept or replaced before the step that
collects it runs again.

One named stage runs through this same process. It keeps the Drive client and
does not ask the opening question or advance to the next stage.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
REDIRECT_PORT = 8765
DOC_RE = re.compile(r"^https://docs\.google\.com/document/(?:u/\d+/)?d/[^/?#]+")
DRIVE_FOLDER_HINT = (
    "Open the Job Search folder so its contents are on screen. Click the browser "
    "address bar and copy that URL. Do not use Share. The address looks like "
    "https://drive.google.com/drive/folders/… or "
    "https://drive.google.com/drive/u/0/folders/…. "
    "Save it with --set artifact_root_url=<url>."
)
DATABASE_ASK = (
    "No database URL is set. "
    "Do you want to use a Postgres database you already started, "
    "or should the wizard guide you through creating one in Vercel? "
    "A private SQLite file does not contain the processed-applications history. "
    "Choose that only when you do not need that history."
)
DATABASE_EXISTING = (
    "Put the connection URL of the Postgres database you already started in .env "
    "as AJSS_DATABASE_URL. DATABASE_URL is the same setting when it is already there, "
    "and the worker reads that name first. Do not paste the URL into the chat. "
    "After the URL is saved, this step checks for the queue tables and creates any that are missing."
)
DATABASE_VERCEL = (
    "Create a Postgres database with Neon in Vercel. "
    "Open the Vercel dashboard and select the project for this system. "
    "Create a project first when the account has none. "
    "Open the Neon integration and choose Create New Neon Account so Vercel creates the database. "
    "Vercel stores the connection string as DATABASE_URL. "
    "On this machine, from the repository root, link the project and pull the variables into a file outside the repo: "
    "vercel link, then vercel env pull /tmp/ajss-vercel.env --yes. "
    "Copy only the DATABASE_URL line from that file into the repo .env and leave the other lines as they are. "
    "Do not print that file or paste the URL into the chat. "
    "Run the checklist again. This step then checks for the queue tables and creates any that are missing."
)
DATABASE_INVALID = (
    "The database URL in .env must be a Postgres URL beginning with postgres:// or postgresql://. "
    "Do not paste the URL into the chat."
)
DATABASE_FAILED = (
    "The database did not answer. Check the URL in .env and run this step again. "
    "Do not paste the URL into the chat."
)
DATABASE_READY = (
    "The database URL is set. This step checks for the queue tables and creates any that are missing."
)
DATABASE_LOCAL = (
    "A local SQLite file is selected. It does not contain the processed-applications history."
)
JOURNAL_HINT = (
    "Open the interviews journal so the document is on screen. Click the browser "
    "address bar and copy that URL. Do not use Share. The address looks like "
    "https://docs.google.com/document/d/…/edit. "
    "Save it with --set interview_journal_url=<url>. "
    "The first worker start adds queue tabs to that document."
)
SECRET_MARKERS = ("SECRET", "TOKEN", "PASSWORD", "DATABASE_URL", "API_KEY")
SETTABLE = {
    "artifact_root_url",
    "interview_journal_url",
    "resume_path",
    "database_mode",
    "database_choice",
    "journal_confirmed",
    "profile_in_drive",
    "resume_in_drive",
    "llm_provider",
    "llm_base_url",
    "llm_model",
    "llm_key",
    "contact_email",
    "contact_phone",
    "calendly_url",
    "wizard",
    "use",
    "replace",
}


def _step_ids():
    from ajss.onboard.profile import REQUIRED_CONTACTS
    names = [
        "google", "drive_root", "journal", "journal_confirmed", "database",
        "resume_path", "resume_in_drive", "llm_provider", "llm_model", "llm_key",
        "profile",
    ]
    names.extend("contact_" + item["field"] for item in REQUIRED_CONTACTS)
    names.append("calendly")
    names.extend(("gmail", "calendar"))
    return names


_SETTING_STEP = {
    "artifact_root_url": "drive_root",
    "interview_journal_url": "journal",
    "journal_confirmed": "journal_confirmed",
    "database_mode": "database",
    "resume_path": "resume_path",
    "resume_in_drive": "resume_in_drive",
    "llm_provider": "llm_provider",
    "llm_base_url": "llm_provider",
    "llm_model": "llm_model",
    "llm_key": "llm_key",
    "contact_email": "contact_email",
    "contact_phone": "contact_phone",
    "calendly_url": "calendly",
    "profile_in_drive": "profile",
}


def project_root(path=None):
    return Path(path or Path(__file__).resolve().parents[2])


def parse_env_file(path):
    values = {}
    file = Path(path)
    if not file.is_file():
        return values
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def merged_environment(path, base=None):
    """Process environment wins. The env file fills only missing names."""
    merged = dict(os.environ if base is None else base)
    for key, value in parse_env_file(path).items():
        merged.setdefault(key, value)
    return merged


def load_project_env(path=None):
    """Load the repo .env into this process without replacing existing values."""
    file = Path(path) if path else project_root() / ".env"
    for key, value in parse_env_file(file).items():
        os.environ.setdefault(key, value)
    return file


def _quote(value):
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError("environment values must be a single line")
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def upsert_env(path, updates, remove=()):
    """Replace named keys and keep every other line. Create the file mode 0600."""
    file = Path(path)
    if file.is_symlink():
        raise RuntimeError("refusing to write secrets through a symlink: " + str(file))
    existing = file.read_text(encoding="utf-8").splitlines() if file.is_file() else []
    pending = dict(updates)
    drop = set(remove)
    output = []
    for raw in existing:
        body = raw.strip()
        prefix = ""
        if body.startswith("export "):
            prefix = "export "
            body = body[len("export "):]
        if "=" in body and not body.startswith("#"):
            key = body.split("=", 1)[0].strip()
            if key in drop:
                continue
            if key in pending:
                output.append(prefix + key + "=" + _quote(pending.pop(key)))
                continue
        output.append(raw)
    for key, value in pending.items():
        output.append(key + "=" + _quote(value))
    text = "\n".join(output)
    if text:
        text += "\n"
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".env-", dir=str(file.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        try:
            os.replace(temporary, file)
        except OSError:
            # A single-file mount cannot be replaced. Write the mounted file in place.
            file.write_text(text, encoding="utf-8")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    os.chmod(file, 0o600)
    return file


def consent_url(client_id, redirect_uri=None, scope=None, include_granted=False):
    redirect_uri = redirect_uri or "http://127.0.0.1:{}/".format(REDIRECT_PORT)
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope or DRIVE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    if include_granted:
        query["include_granted_scopes"] = "true"
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(query)


def exchange_grant(code, client_id, client_secret, redirect_uri, opener):
    """Return the refresh token and the scope Google actually granted."""
    import urllib.parse
    import urllib.request
    payload = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=payload, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with opener(request) as response:
        result = json.loads(response.read().decode("utf-8"))
    refresh = result.get("refresh_token")
    if not refresh:
        raise RuntimeError("Google did not return a refresh token; open the consent link again")
    return refresh, str(result.get("scope") or "")


def exchange_code(code, client_id, client_secret, redirect_uri, opener):
    refresh, _scope = exchange_grant(code, client_id, client_secret, redirect_uri, opener)
    return refresh


def wait_for_code(port, timeout):
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            captured["code"] = dict(parse_qsl(urlsplit(self.path).query)).get("code", "")
            body = b"AJSS received the Google response. You can close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    # Bind every interface so a published container port can receive the browser redirect.
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.timeout = timeout
    try:
        server.handle_request()
    finally:
        server.server_close()
    return captured.get("code") or ""


def listen_for_refresh_token(client_id, client_secret, opener, port=REDIRECT_PORT, timeout=300):
    if not client_id or not client_secret:
        raise RuntimeError("Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env before --listen")
    redirect_uri = "http://127.0.0.1:{}/".format(port)
    print(json.dumps({"open_this_url": consent_url(client_id, redirect_uri)}, indent=2), flush=True)
    code = wait_for_code(port, timeout)
    if not code:
        raise RuntimeError("No Google authorization code arrived. Add the redirect URI and open the link again")
    return exchange_code(code, client_id, client_secret, redirect_uri, opener)


def _link(label, url):
    return {"label": label, "url": url}


VERCEL_DATABASE_LINKS = (
    _link("Create a Neon database in Vercel", "https://vercel.com/marketplace/neon/neon"),
    _link("Open the Vercel dashboard", "https://vercel.com/dashboard"),
)


GOOGLE_LINKS = (
    _link("Select a Google Cloud project", "https://console.cloud.google.com/projectselector2/home/dashboard"),
    _link("Create a project if you have none", "https://console.cloud.google.com/projectcreate"),
    _link("Enable the Google Drive API", "https://console.cloud.google.com/apis/library/drive.googleapis.com"),
    _link("Enable the Google Docs API", "https://console.cloud.google.com/apis/library/docs.googleapis.com"),
    _link("OAuth consent screen", "https://console.cloud.google.com/apis/credentials/consent"),
    _link("Create an OAuth client", "https://console.cloud.google.com/apis/credentials"),
)
LOCAL_NAME = "ajss-local"
OAUTH_CLIENT_NAME = LOCAL_NAME
GOOGLE_SETUP = (
    "Use the Google account that owns the interviews journal. "
    "Select the existing Google Cloud project for this system. "
    "Create a project only when that account has none, then select it. "
    "Inside that project, enable the Drive API and the Docs API, configure the OAuth consent screen, and add your Google account as a test user. "
    "Create an OAuth client in that same project. "
    "Name the client " + OAUTH_CLIENT_NAME + ". "
    "The application type must be Desktop app. "
    "Do not choose Web application, Android, iOS, or Chrome extension. "
    "Leave the checkbox \"Use this client for an AI-powered agent\" unchecked. "
    "This client is for the local worker's Drive and Docs access. "
    "Later steps use the same client for read-only Gmail and Calendar. "
    "It is not an AI agent and it does not use Google MCP. "
    "If the client form asks for a redirect URI, enter http://127.0.0.1:8765/ . "
    "A Desktop app often hides that field; the local command still listens on that address. "
    "Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in the repo .env file. Do not paste them into the chat. "
    "Then run python -m ajss.cli onboard --listen and open the printed link."
)


def _step(identifier, title, ready, instructions, links=()):
    return {
        "id": identifier,
        "title": title,
        "ready": bool(ready),
        "instructions": instructions,
        "links": list(links),
    }


def _google_step(env):
    client_id = (env.get("GOOGLE_CLIENT_ID") or "").strip()
    client_secret = (env.get("GOOGLE_CLIENT_SECRET") or "").strip()
    refresh = (env.get("GOOGLE_REFRESH_TOKEN") or "").strip()
    links = list(GOOGLE_LINKS)
    if client_id and not refresh:
        links.insert(0, _link("Allow Drive access", consent_url(client_id)))
    if refresh and client_id and client_secret:
        return _step("google", "Google account", True,
                     "A refresh token is already stored. This step is left unchanged.", links[:1])
    missing = [name for name, present in (
        ("GOOGLE_CLIENT_ID", client_id),
        ("GOOGLE_CLIENT_SECRET", client_secret),
        ("GOOGLE_REFRESH_TOKEN", refresh),
    ) if not present]
    instructions = GOOGLE_SETUP + " Still needed: " + ", ".join(missing) + "."
    return _step("google", "Google account", False, instructions, links)


def _permission_steps(env, root):
    from adapters.google_events import SCOPES
    from ajss.onboard.permissions import API_LINKS, CONSENT_SCREEN, TITLES, granted, instructions
    client_id = (env.get("GOOGLE_CLIENT_ID") or "").strip()
    steps = []
    for kind in ("gmail", "calendar"):
        text, label = instructions(kind)
        links = [_link(*API_LINKS[kind]), _link(*CONSENT_SCREEN)]
        if client_id:
            links.insert(0, _link(label, consent_url(
                client_id, scope=SCOPES[kind], include_granted=True,
            )))
        steps.append(_step(kind, TITLES[kind], granted(env, root, kind), text, links))
    return steps


def _read_runtime(path):
    file = Path(path)
    if not file.is_file():
        return {}
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("runtime config must be a JSON object")
    drive = data.get("google_drive") or {}
    if not isinstance(drive, dict):
        raise ValueError("google_drive must be a JSON object")
    scheduling = data.get("scheduling") or {}
    if not isinstance(scheduling, dict):
        raise ValueError("scheduling must be a JSON object")
    return data


def _state_path(root):
    return Path(root) / "var" / "onboarding-state.json"


def read_state(root):
    file = _state_path(root)
    if not file.is_file():
        return {}
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("onboarding state must be a JSON object")
    return data


def write_state(root, state):
    file = _state_path(root)
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".onboarding-", suffix=".json", dir=str(file.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, file)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return file


def _confirmed(state, name):
    return state.get(name) is True


def _accepted_map(state):
    value = state.get("accepted")
    if not isinstance(value, dict):
        return {}
    return value


def _accept(state, step_id):
    accepted = dict(_accepted_map(state))
    accepted[step_id] = True
    state["accepted"] = accepted
    if state.get("replacing") == step_id:
        state.pop("replacing", None)


def _drop_accept(state, step_id):
    accepted = dict(_accepted_map(state))
    if step_id not in accepted:
        return
    accepted.pop(step_id, None)
    if accepted:
        state["accepted"] = accepted
    else:
        state.pop("accepted", None)


def _safe_provider_value(raw):
    text = (raw or "").strip()
    lowered = text.casefold()
    if (
        not text
        or len(text) > 120
        or any(ch.isspace() for ch in text)
        or "key" in lowered
        or "secret" in lowered
        or "token" in lowered
        or text.startswith("sk-")
    ):
        return "the current value"
    return text


def _llm_steps(env, state):
    from adapters.llm.providers import PROVIDERS, selection
    selected = selection(env)
    raw = (env.get("AJSS_LLM_PROVIDER") or "").strip()
    names = ", ".join(PROVIDERS)
    if selected.phase == "choose":
        provider_text = (
            "Cover letters and the applicant profile use an OpenAI-compatible chat API. "
            "Say which provider to use. The wizard writes AJSS_LLM_PROVIDER. "
            "Choices: " + names + ", or local for a custom server or one running on this machine. "
            "Save it with --set llm_provider=<choice>. "
            "A coding-agent login is not used for this call."
        )
    elif selected.phase == "need_url":
        provider_text = (
            "A custom or local provider needs its OpenAI-compatible base URL. "
            "The wizard stores that URL in AJSS_LLM_PROVIDER. "
            "The worker runs in Docker, so a server on this machine is "
            "http://host.docker.internal:<port>/v1. "
            "Example for Ollama: http://host.docker.internal:11434/v1. "
            "Use http://127.0.0.1:<port>/v1 only when the worker is not in Docker. "
            "Save it with --set llm_base_url=<url>."
        )
    elif selected.phase == "invalid":
        provider_text = (
            "AJSS_LLM_PROVIDER (" + _safe_provider_value(raw) + ") is not a known provider "
            "or an http(s) base URL. Save --set llm_provider= with one of: "
            + names + ", local."
        )
    elif selected.phase == "url":
        provider_text = "AJSS_LLM_PROVIDER is " + selected.base_url + "."
    else:
        provider_text = (
            "AJSS_LLM_PROVIDER is " + selected.name + ". Requests go to "
            + selected.base_url + " using model " + selected.model + "."
        )
    if selected.phase == "url" and not selected.model:
        model_text = (
            "AJSS_LLM_PROVIDER is " + selected.base_url + ". "
            "This server needs a model name. Save it with --set llm_model=<name>."
        )
    elif selected.model:
        model_text = (
            "The model is " + selected.model + ". Change it with --set llm_model=<name>."
        )
    else:
        model_text = "Choose a provider before naming a model."
    if selected.phase == "named":
        key_text = (
            "AJSS_LLM_PROVIDER is " + selected.name + ". Requests go to "
            + selected.base_url + " using model " + selected.model + ". "
            "Name the key " + LOCAL_NAME + ". "
            "Put the key in .env as AJSS_LLM_API_KEY. Do not paste the key into the chat. "
            "A coding-agent login is not this key."
        )
        if selected.name == "grok":
            key_text += " An existing XAI_API_KEY is also accepted for Grok."
        elif selected.name == "openai":
            key_text += " An existing OPENAI_API_KEY is also accepted for OpenAI."
        key_links = [_link(selected.key_label, selected.key_url)] if selected.key_url else []
    elif selected.phase == "url":
        key_text = (
            "AJSS_LLM_PROVIDER is " + selected.base_url + ". "
            "If that server requires a key, name it " + LOCAL_NAME + " and put AJSS_LLM_API_KEY in .env. "
            "Do not paste the key into the chat. "
            "If it does not require a key, confirm with --set llm_key=none."
        )
        key_links = []
    else:
        key_text = "Choose a provider before adding a model key."
        key_links = []
    return [
        _step("llm_provider", "Model provider", selected.provider_ready, provider_text),
        _step("llm_model", "Model name", selected.model_ready, model_text),
        _step(
            "llm_key", "Model API key",
            selected.key_ready(state.get("llm_key_declined") is True),
            key_text, key_links,
        ),
    ]


def _profile_instructions():
    from ajss.onboard.profile import REQUIRED_CONTACTS
    names = [item["label"] for item in REQUIRED_CONTACTS]
    required = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    verb = "is" if len(names) == 1 else "are"
    return (
        "The system creates Applicant/applicant_profile.json from the résumé PDF. "
        "You do not provide this file. Running onboard writes it to the Drive folder "
        "and leaves it unchanged when the résumé is unchanged. "
        + required[:1].upper() + required[1:] + " " + verb + " required. "
        "If the résumé omits one, the next step asks for it. "
        "A Calendly link is asked next and stored on that same profile. "
        "Each cover letter includes it."
    )


def _contact_steps(state):
    from ajss.onboard.profile import REQUIRED_CONTACTS
    extracted = bool(state.get("profile_sha256"))
    recorded = state.get("profile_missing")
    if extracted and isinstance(recorded, list):
        missing = set(recorded)
    else:
        missing = {item["field"] for item in REQUIRED_CONTACTS}
    steps = []
    for item in REQUIRED_CONTACTS:
        ready = extracted and item["field"] not in missing
        if ready:
            text = "The " + item["label"] + " is saved on the applicant profile."
        else:
            text = (
                "The résumé did not include " + item["article"] + " " + item["label"] + ". "
                "What is your " + item["label"] + "? "
                "The answer is written onto Applicant/applicant_profile.json."
            )
        steps.append(_step("contact_" + item["field"], item["title"], ready, text))
    return steps


def _calendly_url(config):
    scheduling = config.get("scheduling") or {}
    if not isinstance(scheduling, dict):
        return ""
    return str(scheduling.get("calendly_url") or "").strip()


def parse_calendly_url(value):
    text = value.strip()
    if text.casefold() == "none":
        return ""
    from ajss.onboard.profile import validate_calendly_url
    return validate_calendly_url(text)


def supplied_calendly(state, config):
    """None means the user has not answered. An empty string means they have no link."""
    if _confirmed(state, "calendly_declined"):
        return ""
    chosen = str(state.get("calendly_url") or "").strip()
    if chosen:
        return chosen
    legacy = _calendly_url(config)
    return legacy or None


def _calendly_step(state):
    saved = str(state.get("calendly_on_profile") or "").strip()
    declined = _confirmed(state, "calendly_declined")
    if saved and not declined:
        text = (
            "The Calendly link is saved on the applicant profile and is added to each cover letter."
        )
    elif declined:
        text = "No Calendly link will be added to cover letters."
    else:
        text = (
            "What is your Calendly link? It looks like https://calendly.com/name/meeting. "
            "The answer is written onto Applicant/applicant_profile.json and added to each cover letter. "
            "If you do not have one, say so."
        )
    return _step(
        "calendly", "Calendly link",
        declined or bool(saved),
        text,
        [_link("Open Calendly", "https://calendly.com/")],
    )


def _database_fingerprint(url):
    import hashlib
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _url_is_held(state, url):
    """A replaced database stays on the choice until its URL changes."""
    if "database_hold" not in state:
        return False
    if not url:
        return True
    return (state.get("database_hold") or "") == _database_fingerprint(url)


def _database_step(env, state):
    from ajss.onboard.database import is_postgres_url
    from ajss.settings import database_url
    url = database_url(env)
    local = env.get("AJSS_ALLOW_LOCAL_SQLITE") == "1"
    held = _url_is_held(state, url)
    failed = (
        not held
        and bool(url)
        and state.get("database_check") == "failed"
        and state.get("database_check_for") == _database_fingerprint(url)
    )
    if url and not held and not is_postgres_url(url):
        return _step("database", "Processed-applications database", False, DATABASE_INVALID, VERCEL_DATABASE_LINKS)
    if failed:
        return _step("database", "Processed-applications database", False, DATABASE_FAILED)
    if url and not held:
        return _step("database", "Processed-applications database", True, DATABASE_READY)
    if local and not url:
        return _step("database", "Processed-applications database", True, DATABASE_LOCAL)
    choice = state.get("database_choice")
    if choice == "vercel":
        return _step("database", "Processed-applications database", False, DATABASE_VERCEL, VERCEL_DATABASE_LINKS)
    if choice == "existing":
        return _step("database", "Processed-applications database", False, DATABASE_EXISTING)
    return _step("database", "Processed-applications database", False, DATABASE_ASK, VERCEL_DATABASE_LINKS)


def _database_is_current(env, root, runtime_path, stage=""):
    if stage and stage != "database":
        return False
    state = read_state(root) if _state_path(root).is_file() else {}
    if state.get("replacing"):
        return False
    if stage != "database" and state.get("wizard") != "yes":
        return False
    from ajss.onboard.database import is_postgres_url
    from ajss.settings import database_url
    url = database_url(env)
    if not url or not is_postgres_url(url) or _url_is_held(state, url):
        return False
    if stage == "database":
        return True
    report = assess(env, root, runtime_path)
    cursor, _mode = _cursor(report["steps"], state)
    return bool(cursor) and cursor["id"] == "database"


def _remember_database(root, resources, url, schema):
    """Check the current database once the wizard is on that step."""
    fingerprint = _database_fingerprint(url)
    cached = resources.get("database_tables") if isinstance(resources, dict) else None
    if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint and cached.get("detail"):
        _clear_database_hold(root)
        if isinstance(resources, dict):
            resources["database_note"] = cached["detail"]
        return
    try:
        created = schema(url)
    except Exception:
        state = read_state(root) if _state_path(root).is_file() else {}
        state["database_check"] = "failed"
        state["database_check_for"] = fingerprint
        write_state(root, state)
        if isinstance(resources, dict):
            resources.pop("database_note", None)
        return
    detail = "The queue tables were created." if created else "The queue tables are already there."
    _clear_database_hold(root)
    if isinstance(resources, dict):
        resources["database_tables"] = {"fingerprint": fingerprint, "detail": detail}
        resources["database_note"] = detail


def _clear_database_hold(root):
    state = read_state(root) if _state_path(root).is_file() else {}
    changed = False
    for key in ("database_check", "database_check_for", "database_hold"):
        if key in state:
            state.pop(key, None)
            changed = True
    if changed:
        write_state(root, state)


def assess(env, root, runtime_path):
    root = Path(root)
    try:
        config = _read_runtime(runtime_path)
        drive = config.get("google_drive") or {}
        runtime_error = ""
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        config = {}
        drive = {}
        runtime_error = str(exc)
    state = read_state(root) if _state_path(root).is_file() else {}
    journal = str(drive.get("interview_journal_url") or "")
    artifact = str(drive.get("artifact_root_url") or "")
    from adapters.google.drive import is_drive_folder_url
    journal_ok = bool(DOC_RE.search(journal))
    drive_ok = is_drive_folder_url(artifact)
    from ajss.settings import resume_drive_path
    resume = resume_drive_path(env)
    steps = [
        _google_step(env),
        _step(
            "drive_root", "Drive folder", drive_ok,
            runtime_error or DRIVE_FOLDER_HINT,
            [_link("Open Google Drive", "https://drive.google.com/drive/my-drive")],
        ),
        _step(
            "journal", "Interviews journal", journal_ok,
            JOURNAL_HINT,
            [_link("Open Google Docs", "https://docs.google.com/")],
        ),
        _step(
            "journal_confirmed", "Confirm the journal",
            journal_ok and _confirmed(state, "journal_confirmed"),
            "Confirm this exact document is the interviews journal with "
            "--set journal_confirmed=yes. Changing the URL clears the confirmation.",
        ),
        _database_step(env, state),
        _step(
            "resume_path", "Résumé location", bool(resume),
            "Upload the résumé PDF into the Drive folder and save its path with "
            "--set resume_path=Applicant/resume.pdf. The path is relative to the Drive root.",
        ),
        _step(
            "resume_in_drive", "Résumé is in Drive",
            bool(resume) and _confirmed(state, "resume_in_drive"),
            "After the PDF is in that Drive path, confirm with --set resume_in_drive=yes.",
        ),
        *_llm_steps(env, state),
        _step(
            "profile", "Applicant profile",
            bool(resume) and bool(state.get("profile_sha256")),
            _profile_instructions(),
        ),
        *_contact_steps(state),
        _calendly_step(state),
        *_permission_steps(env, root),
    ]
    pending = [step for step in steps if not step["ready"]]
    return {
        "ready": not pending,
        "next": pending[0] if pending else None,
        "steps": steps,
        "start_when_ready": "python -m ajss.cli run",
        "note": "run adds queue tabs to the confirmed journal. Start it only after the user agrees.",
    }


def _yes(value):
    text = value.strip().casefold()
    if text in {"yes", "true", "1"}:
        return True
    if text in {"no", "false", "0"}:
        return False
    raise ValueError("expected yes or no")


def apply_settings(assignments, root, runtime_path, env_path):
    root = Path(root)
    state = read_state(root) if _state_path(root).is_file() else {}
    drive = {}
    runtime = Path(runtime_path)
    if runtime.is_file():
        current = json.loads(runtime.read_text(encoding="utf-8"))
        if isinstance(current, dict) and isinstance(current.get("google_drive"), dict):
            drive = dict(current["google_drive"])
    env_updates = {}
    env_removals = []
    urls_changed = False
    for key, value in assignments:
        if any(marker in key.upper() for marker in SECRET_MARKERS) or key not in SETTABLE:
            raise ValueError("unsupported onboarding setting: " + key)
        if key == "artifact_root_url":
            from adapters.google.drive import is_drive_folder_url
            if not is_drive_folder_url(value):
                raise ValueError(
                    "Copy the folder URL from the browser address bar after the folder "
                    "is open. Do not use Share. The address contains /drive/folders/."
                )
            drive["artifact_root_url"] = value
            urls_changed = True
        elif key == "interview_journal_url":
            if not DOC_RE.search(value):
                raise ValueError("Journal URL must be a Google Docs document URL")
            if value != drive.get("interview_journal_url"):
                state["journal_confirmed"] = False
                _drop_accept(state, "journal")
                _drop_accept(state, "journal_confirmed")
            drive["interview_journal_url"] = value
            urls_changed = True
        elif key == "resume_path":
            if not value or value.startswith("/") or ".." in Path(value).parts:
                raise ValueError("Résumé path must be relative to the Drive root")
            from ajss.settings import RESUME_DRIVE_PATH, resume_drive_path
            if value != resume_drive_path(parse_env_file(env_path)):
                state.pop("profile_sha256", None)
                state.pop("profile_missing", None)
                for step_id in ("resume_path", "resume_in_drive", "profile", "contact_email", "contact_phone"):
                    _drop_accept(state, step_id)
            env_updates[RESUME_DRIVE_PATH] = value
            env_removals.append("AJSS_RESUME_DRIVE_PATH")
        elif key == "database_mode":
            if value == "local":
                env_updates["AJSS_ALLOW_LOCAL_SQLITE"] = "1"
            elif value == "neon":
                env_updates["AJSS_ALLOW_LOCAL_SQLITE"] = "0"
            else:
                raise ValueError("database_mode must be local or neon")
        elif key == "database_choice":
            choice = value.strip().casefold()
            if choice not in {"existing", "vercel"}:
                raise ValueError("database_choice must be existing or vercel")
            state["database_choice"] = choice
            if choice == "existing":
                from ajss.settings import database_url
                if database_url(merged_environment(env_path)):
                    state.pop("database_hold", None)
            if state.get("replacing") == "database":
                state.pop("replacing", None)
        elif key == "journal_confirmed":
            state["journal_confirmed"] = _yes(value)
        elif key == "profile_in_drive":
            state["profile_in_drive"] = _yes(value)
        elif key == "resume_in_drive":
            state["resume_in_drive"] = _yes(value)
        elif key == "llm_provider":
            from adapters.llm.providers import parse_provider_choice
            env_updates["AJSS_LLM_PROVIDER"] = parse_provider_choice(value)
            state.pop("llm_key_declined", None)
        elif key == "llm_base_url":
            from adapters.llm.providers import normalize_base_url
            env_updates["AJSS_LLM_PROVIDER"] = normalize_base_url(value)
            state.pop("llm_key_declined", None)
        elif key == "llm_model":
            if not re.fullmatch(r"[A-Za-z0-9_.:/@+-]+", value):
                raise ValueError("llm_model must be a model id")
            env_updates["AJSS_LLM_MODEL"] = value
        elif key == "llm_key":
            if value.casefold() != "none":
                raise ValueError(
                    "Put AJSS_LLM_API_KEY in .env. Pass llm_key=none only when the server needs no key."
                )
            from adapters.llm.providers import is_base_url
            current = env_updates.get("AJSS_LLM_PROVIDER", parse_env_file(env_path).get("AJSS_LLM_PROVIDER", ""))
            if not is_base_url(current):
                raise ValueError("A named provider needs AJSS_LLM_API_KEY in .env.")
            state["llm_key_declined"] = True
        elif key in {"contact_email", "contact_phone"}:
            from ajss.onboard.profile import validate_contact
            field = key[len("contact_"):]
            contacts = dict(state.get("contacts") or {})
            contacts[field] = validate_contact(field, value)
            state["contacts"] = contacts
        elif key == "calendly_url":
            chosen = parse_calendly_url(value)
            if chosen:
                state["calendly_url"] = chosen
                state.pop("calendly_declined", None)
            else:
                state["calendly_declined"] = True
                state.pop("calendly_url", None)
        elif key == "wizard":
            choice = value.strip().casefold()
            if choice not in {"yes", "no"}:
                raise ValueError("wizard must be yes or no")
            state["wizard"] = choice
            if choice == "no":
                state.pop("replacing", None)
        elif key == "use":
            step_id = value.strip()
            if step_id not in _step_ids():
                raise ValueError("unknown setup step: " + step_id)
            _accept(state, step_id)
        elif key == "replace":
            step_id = value.strip()
            if step_id not in _step_ids():
                raise ValueError("unknown setup step: " + step_id)
            state["replacing"] = step_id
            _drop_accept(state, step_id)
            if step_id == "profile":
                state.pop("profile_sha256", None)
                state.pop("profile_missing", None)
            if step_id == "database":
                from ajss.settings import database_url
                current = database_url(merged_environment(env_path))
                state["database_hold"] = _database_fingerprint(current) if current else ""
                state.pop("database_choice", None)
                state.pop("database_check", None)
                state.pop("database_check_for", None)
            if step_id in {"gmail", "calendar"}:
                from ajss.onboard.permissions import clear_grant
                clear_grant(root, env_path, step_id)
        step_id = _SETTING_STEP.get(key)
        if step_id:
            _accept(state, step_id)
    if urls_changed:
        from ajss.onboard.google import write_runtime_config
        write_runtime_config(
            {
                "document_url": drive.get("interview_journal_url") or "",
                "drive_root_url": drive.get("artifact_root_url") or "",
            },
            runtime,
        )
    if env_updates:
        upsert_env(env_path, env_updates, remove=env_removals)
    if state or _state_path(root).is_file():
        write_state(root, state)
    return state


def supplied_contacts(env, state):
    """Résumé values win later. A saved answer wins over the environment."""
    from ajss.onboard.profile import REQUIRED_CONTACTS
    found = {}
    stored = state.get("contacts") or {}
    for item in REQUIRED_CONTACTS:
        chosen = str(stored.get(item["field"]) or "").strip()
        if not chosen:
            chosen = str(env.get(item["env"]) or "").strip()
        if chosen:
            found[item["field"]] = chosen
    return found


def _contacts_need_sync(root, env):
    state = read_state(root) if _state_path(root).is_file() else {}
    if not state.get("profile_sha256") or not isinstance(state.get("profile_missing"), list):
        return False
    supplied = supplied_contacts(env, state)
    return any(field in supplied for field in state["profile_missing"])


def _calendly_needs_sync(root, runtime_path):
    state = read_state(root) if _state_path(root).is_file() else {}
    if not state.get("profile_sha256"):
        return False
    try:
        config = _read_runtime(runtime_path)
    except (OSError, ValueError, json.JSONDecodeError):
        config = {}
    wanted = supplied_calendly(state, config)
    if wanted is None:
        return False
    return str(state.get("calendly_on_profile") or "") != wanted


def _write_json(path, payload):
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".runtime-", suffix=".json", dir=str(file.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        try:
            os.replace(temporary, file)
        except OSError:
            file.write_text(text, encoding="utf-8")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _clear_runtime_calendly(runtime_path):
    """Drop a scheduling link left in runtime config. The profile is the stored copy."""
    file = Path(runtime_path)
    if not file.is_file():
        return
    config = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        return
    scheduling = config.get("scheduling")
    if not isinstance(scheduling, dict) or "calendly_url" not in scheduling:
        return
    scheduling = dict(scheduling)
    scheduling.pop("calendly_url", None)
    if scheduling:
        config["scheduling"] = scheduling
    else:
        config.pop("scheduling", None)
    _write_json(file, config)


def _google_fingerprint(env):
    import hashlib
    parts = [
        env.get("GOOGLE_REFRESH_TOKEN") or "",
        env.get("GOOGLE_CLIENT_ID") or "",
        env.get("GOOGLE_CLIENT_SECRET") or "",
        env.get("GOOGLE_ACCESS_TOKEN") or "",
        env.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "",
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


class WarmDrive:
    """File bytes kept for the life of one wizard process.

    A later read of a path this process already read or wrote does not download
    it again. A write updates that copy and uploads it. A missing file is not
    remembered, so a later upload can still be found.
    """

    def __init__(self, store):
        self.store = store
        self.files = {}

    def read_path(self, path):
        from adapters.google.paths import normalize_file_path
        key = normalize_file_path(path)
        if key in self.files:
            return {"content": self.files[key]}
        found = self.store.read_path(key)
        content = found["content"]
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.files[key] = bytes(content)
        return {"content": self.files[key], "file": found.get("file")}

    def save_path(self, path, content, mime_type=None, idempotency_key=None):
        from adapters.google.paths import normalize_file_path
        key = normalize_file_path(path)
        payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        saved = self.store.save_path(
            key, payload, mime_type=mime_type, idempotency_key=idempotency_key,
        )
        self.files[key] = payload
        return saved

    def __getattr__(self, name):
        return getattr(self.store, name)


def _drive_store(env, runtime_path, resources):
    """Reuse the Drive client while the Google credentials and folder stay the same."""
    from adapters.google.auth import token_from_environment
    from adapters.google.drive import GoogleDriveArtifactStore
    from ajss.settings import load
    cfg = load(path=runtime_path)
    root_url = cfg["google_drive"]["artifact_root_url"]
    fingerprint = _google_fingerprint(env)
    store = resources.get("store")
    if (
        store is not None
        and resources.get("fingerprint") == fingerprint
        and resources.get("root_url") == root_url
    ):
        return store
    token = token_from_environment()
    if token is None:
        raise RuntimeError("Google credentials are not available")
    store = GoogleDriveArtifactStore(token, root_url)
    keep = getattr(store, "keep_alive", None)
    if callable(keep):
        keep()
    wrapped = WarmDrive(store)
    resources["fingerprint"] = fingerprint
    resources["root_url"] = root_url
    resources["store"] = wrapped
    return wrapped


def _warm_drive(env, runtime_path, resources):
    """Open the Drive client once the folder is known. Later steps reuse it."""
    if not any(env.get(name) for name in (
        "GOOGLE_REFRESH_TOKEN", "GOOGLE_ACCESS_TOKEN", "GOOGLE_SERVICE_ACCOUNT_JSON",
    )):
        return
    import os
    previous = dict(os.environ)
    os.environ.update({key: value for key, value in env.items() if value})
    try:
        _drive_store(env, runtime_path, resources)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return
    finally:
        os.environ.clear()
        os.environ.update(previous)


def publish_applicant_profile(root, env, runtime_path, resources=None):
    """Create the profile from the résumé, or fill a contact or Calendly link on the saved JSON."""
    from ajss.onboard.profile import ensure_applicant_profile, patch_profile_contacts
    from ajss.settings import resume_drive_path
    import os
    if resources is None:
        resources = {}
    state = read_state(root) if _state_path(root).is_file() else {}
    try:
        config = _read_runtime(runtime_path)
    except (OSError, ValueError, json.JSONDecodeError):
        config = {}
    calendly = supplied_calendly(state, config)
    previous = dict(os.environ)
    os.environ.update({key: value for key, value in env.items() if value})
    try:
        store = _drive_store(env, runtime_path, resources)
        contacts = supplied_contacts(env, state)
        if state.get("profile_sha256"):
            try:
                result = patch_profile_contacts(store, contacts, calendly_url=calendly)
            except KeyError:
                result = ensure_applicant_profile(
                    store, resume_drive_path(env), contacts=contacts, calendly_url=calendly,
                )
        else:
            result = ensure_applicant_profile(
                store, resume_drive_path(env), contacts=contacts, calendly_url=calendly,
            )
    finally:
        os.environ.clear()
        os.environ.update(previous)
    state = read_state(root) if _state_path(root).is_file() else state
    state["profile_sha256"] = result["sha256"]
    state["profile_missing"] = result["missing"]
    state["calendly_on_profile"] = result.get("calendly_url") or ""
    if state["calendly_on_profile"] and not _confirmed(state, "calendly_declined"):
        state["calendly_url"] = state["calendly_on_profile"]
    write_state(root, state)
    if calendly is not None:
        try:
            _clear_runtime_calendly(runtime_path)
        except OSError:
            pass
    return result


def _displays(env, root, runtime_path):
    """Short descriptions of stored values. Secrets and contact details stay out."""
    try:
        config = _read_runtime(runtime_path)
    except (OSError, ValueError, json.JSONDecodeError):
        config = {}
    state = read_state(root) if _state_path(root).is_file() else {}
    drive = config.get("google_drive") or {}
    from adapters.google.drive import is_drive_folder_url
    from adapters.llm.providers import selection
    selected = selection(env)
    values = {}
    if (env.get("GOOGLE_REFRESH_TOKEN") or "").strip() and (env.get("GOOGLE_CLIENT_ID") or "").strip() and (env.get("GOOGLE_CLIENT_SECRET") or "").strip():
        values["google"] = "a refresh token is stored"
    artifact = str(drive.get("artifact_root_url") or "")
    if is_drive_folder_url(artifact):
        values["drive_root"] = artifact
    journal = str(drive.get("interview_journal_url") or "")
    if DOC_RE.search(journal):
        values["journal"] = journal
    if _confirmed(state, "journal_confirmed"):
        values["journal_confirmed"] = "confirmed for this document"
    from ajss.settings import database_url
    stored_database = database_url(env)
    if stored_database and not _url_is_held(state, stored_database):
        values["database"] = "set in .env"
    elif not stored_database and env.get("AJSS_ALLOW_LOCAL_SQLITE") == "1":
        values["database"] = "local SQLite, without the processed-applications history"
    from ajss.settings import resume_drive_path
    resume = resume_drive_path(env)
    if resume:
        values["resume_path"] = resume
    if _confirmed(state, "resume_in_drive"):
        values["resume_in_drive"] = "confirmed in Drive"
    if selected.provider_ready:
        values["llm_provider"] = selected.name if selected.phase == "named" else selected.base_url
    if selected.model_ready:
        values["llm_model"] = selected.model
    if selected.key_ready(state.get("llm_key_declined") is True):
        if state.get("llm_key_declined") is True and not selected.api_key:
            values["llm_key"] = "no key is required"
        else:
            values["llm_key"] = "a key is stored"
    if state.get("profile_sha256"):
        values["profile"] = "Applicant/applicant_profile.json"
    recorded = state.get("profile_missing")
    if state.get("profile_sha256") and isinstance(recorded, list):
        from ajss.onboard.profile import REQUIRED_CONTACTS
        missing = set(recorded)
        for item in REQUIRED_CONTACTS:
            if item["field"] not in missing:
                values["contact_" + item["field"]] = "saved on the applicant profile"
    if _confirmed(state, "calendly_declined"):
        values["calendly"] = "no link"
    elif str(state.get("calendly_on_profile") or "").strip():
        values["calendly"] = str(state.get("calendly_on_profile")).strip()
    from ajss.onboard.permissions import granted
    if granted(env, root, "gmail"):
        values["gmail"] = "read-only access is stored"
    if granted(env, root, "calendar"):
        values["calendar"] = "read-only access is stored"
    return values


def _confirm_instructions(step_id, title, value):
    if step_id == "google":
        return "A Google refresh token is already stored. Use this account, or connect a different one?"
    if step_id == "journal_confirmed":
        return "This document is already confirmed as the interviews journal. Use this confirmation, or choose a different document?"
    if step_id == "database":
        if "local SQLite" in value:
            return (
                "A local SQLite database is already selected. "
                "It does not contain the processed-applications history. "
                "Use this database, or choose a different one?"
            )
        return "The processed-applications database is already configured. Use this database, or choose a different one?"
    if step_id == "resume_in_drive":
        return "The résumé is already confirmed in Drive. Use this confirmation, or choose a different file?"
    if step_id == "llm_key":
        if value == "no key is required":
            return "This model server is already set to use no key. Keep that, or provide a key?"
        return "A model key is already stored. Use this key, or provide a different one?"
    if step_id == "profile":
        return "Applicant/applicant_profile.json is already saved. Use this profile, or create it again from the résumé?"
    if step_id == "contact_email":
        return "An email address is already saved on the applicant profile. Use this email address, or provide a different one?"
    if step_id == "contact_phone":
        return "A phone number is already saved on the applicant profile. Use this phone number, or provide a different one?"
    if step_id == "calendly" and value == "no link":
        return "No Calendly link is stored. Keep it that way, or add a link?"
    if step_id == "gmail":
        return "Read-only Gmail access is already stored. Use this access, or grant it again?"
    if step_id == "calendar":
        return "Read-only Calendar access is already stored. Use this access, or grant it again?"
    return "The current " + title + " is " + value + ". Use this value, or replace it?"


def _cursor(steps, state):
    replacing = state.get("replacing")
    accepted = _accepted_map(state)
    if isinstance(replacing, str) and replacing:
        for step in steps:
            if step["id"] == replacing:
                return step, "retrieve"
    for step in steps:
        if not step["ready"]:
            return step, "retrieve"
        if not accepted.get(step["id"]):
            return step, "confirm"
    return None, None


def _opening_text(present, missing_titles, sanity, choice):
    parts = []
    if present:
        listed = ". ".join(item["label"] + ": " + item["value"] for item in present)
        parts.append("These values are already set. " + listed + ".")
    if choice == "no":
        parts.append("The wizard will leave the stored values as they are.")
        if missing_titles:
            parts.append("These are still missing: " + ", ".join(missing_titles) + ".")
        elif sanity and sanity.get("ok"):
            parts.append("The checks passed.")
        elif sanity:
            failed = [item for item in sanity.get("checks") or [] if not item.get("ok")]
            detail = ". ".join(item["label"] + ": " + item["detail"] for item in failed)
            parts.append("These checks did not pass. " + detail + ".")
        return " ".join(parts)
    if not missing_titles:
        parts.append("It seems everything required is already set.")
        if sanity and sanity.get("ok"):
            passed = ". ".join(item["label"] + ": " + item["detail"] for item in sanity.get("checks") or [])
            parts.append("The checks passed. " + passed + ".")
            parts.append("Do you still want to run the wizard?")
        elif sanity:
            failed = [item for item in sanity.get("checks") or [] if not item.get("ok")]
            detail = ". ".join(item["label"] + ": " + item["detail"] for item in failed)
            parts.append("These checks did not pass. " + detail + ".")
            parts.append("Do you want to run the wizard to correct them?")
        else:
            parts.append("Do you still want to run the wizard?")
    else:
        parts.append("These are still missing: " + ", ".join(missing_titles) + ".")
        parts.append("Do you want to proceed with the wizard?")
    return " ".join(parts)


def _sanity_fingerprint(env, root, runtime_path):
    import hashlib
    from ajss.settings import resume_drive_path
    try:
        config = _read_runtime(runtime_path)
    except (OSError, ValueError, json.JSONDecodeError):
        config = {}
    state = read_state(root) if _state_path(root).is_file() else {}
    drive = config.get("google_drive") or {}
    parts = [
        env.get("GOOGLE_REFRESH_TOKEN") or "",
        env.get("GOOGLE_CLIENT_ID") or "",
        env.get("GOOGLE_CLIENT_SECRET") or "",
        env.get("GOOGLE_ACCESS_TOKEN") or "",
        env.get("DATABASE_URL") or "",
        env.get("AJSS_DATABASE_URL") or "",
        env.get("AJSS_ALLOW_LOCAL_SQLITE") or "",
        env.get("AJSS_LLM_API_KEY") or "",
        env.get("AJSS_LLM_PROVIDER") or "",
        env.get("AJSS_LLM_MODEL") or "",
        env.get("OPENAI_API_KEY") or "",
        env.get("XAI_API_KEY") or "",
        resume_drive_path(env),
        str(drive.get("interview_journal_url") or ""),
        str(drive.get("artifact_root_url") or ""),
        str(state.get("profile_sha256") or ""),
        str(state.get("calendly_on_profile") or ""),
        str(state.get("calendly_declined") or ""),
        json.dumps(state.get("profile_missing") or []),
        env.get("GOOGLE_GMAIL_REFRESH_TOKEN") or "",
        env.get("GOOGLE_CALENDAR_REFRESH_TOKEN") or "",
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _load_sanity(env, root, runtime_path, resources, runner):
    fingerprint = _sanity_fingerprint(env, root, runtime_path)
    cached = resources.get("sanity") if isinstance(resources, dict) else None
    if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint:
        return cached.get("result")
    try:
        result = runner(env, root, runtime_path, resources)
    except Exception:
        result = None
    if not isinstance(result, dict):
        result = {
            "ok": False,
            "checks": [{
                "id": "sanity",
                "label": "Setup checks",
                "ok": False,
                "detail": "The checks could not be run.",
            }],
        }
    if isinstance(resources, dict):
        resources["sanity"] = {"fingerprint": fingerprint, "result": result}
    return result


def _stage_cursor(steps, state, stage):
    chosen = next((step for step in steps if step["id"] == stage), None)
    if chosen is None:
        raise ValueError("unknown setup step: " + stage)
    if state.get("replacing") == stage or not chosen["ready"]:
        return chosen, "retrieve"
    if not _accepted_map(state).get(stage):
        return chosen, "confirm"
    return None, None


def _stage_done(stage):
    if stage == "gmail":
        return "Read-only Gmail access is already stored."
    if stage == "calendar":
        return "Read-only Calendar access is already stored."
    return "This stage is already set."


def _describe_stage(report, steps, state, displays, stage, resources):
    cursor, mode = _stage_cursor(steps, state, stage)
    present = []
    missing_titles = []
    for step in steps:
        if step["id"] != stage:
            continue
        if step["ready"]:
            present.append({
                "id": step["id"],
                "label": step["title"],
                "value": displays.get(step["id"]) or "saved",
            })
        else:
            missing_titles.append(step["title"])
    nxt = None
    opening = ""
    if cursor is not None:
        nxt = dict(cursor)
        nxt["mode"] = mode
        if mode == "confirm":
            current = displays.get(cursor["id"]) or "saved"
            nxt["current"] = current
            nxt["instructions"] = _confirm_instructions(cursor["id"], cursor["title"], current)
            nxt["links"] = []
            note = resources.get("database_note") if isinstance(resources, dict) else ""
            if cursor["id"] == "database" and note:
                nxt["instructions"] = note + " " + nxt["instructions"]
    else:
        opening = _stage_done(stage)
    described = dict(report)
    described.update({
        "gate": "run",
        "present": present,
        "missing": missing_titles,
        "opening": opening,
        "sanity": None,
        "can_start": False,
        "next": nxt,
        "stage": stage,
    })
    return described


def describe_wizard(report, env, root, runtime_path, resources=None, sanity_runner=None, just_used=(), fresh=(), stage=""):
    """Add the opening inventory, and ask before replacing a value that is already stored."""
    if resources is None:
        resources = {}
    root = Path(root)
    state = read_state(root) if _state_path(root).is_file() else {}
    steps = report["steps"]
    ready_ids = {step["id"] for step in steps if step["ready"]}
    for step_id in just_used:
        if step_id not in ready_ids:
            raise ValueError("That value is not set yet: " + step_id)
    accepted = dict(_accepted_map(state))
    kept = {}
    for step_id, value in accepted.items():
        if step_id in ready_ids or step_id in fresh:
            kept[step_id] = True if value else True
    if kept != accepted or not isinstance(state.get("accepted"), dict) and state.get("accepted"):
        if kept:
            state["accepted"] = kept
        else:
            state.pop("accepted", None)
        write_state(root, state)
    displays = _displays(env, root, runtime_path)
    if stage:
        return _describe_stage(report, steps, state, displays, stage, resources)
    present = []
    missing_titles = []
    for step in steps:
        if step["ready"]:
            present.append({
                "id": step["id"],
                "label": step["title"],
                "value": displays.get(step["id"]) or "saved",
            })
        else:
            missing_titles.append(step["title"])
    choice = state.get("wizard")
    if choice not in {"yes", "no"}:
        choice = None
    cursor, mode = _cursor(steps, state)
    needs_sanity = bool(report["ready"]) and (choice != "yes" or cursor is None)
    sanity = None
    if needs_sanity:
        from ajss.onboard.sanity import run_sanity
        sanity = _load_sanity(env, root, runtime_path, resources, sanity_runner or run_sanity)
    if present and choice != "yes":
        gate = "stop" if choice == "no" else "ask"
    else:
        gate = "run"
    opening = ""
    nxt = None
    if gate == "ask":
        opening = _opening_text(present, missing_titles, sanity, None)
        nxt = _step("wizard", "Existing setup", False, opening)
        nxt["mode"] = "ask"
    elif gate == "stop":
        opening = _opening_text(present, missing_titles, sanity, "no")
    else:
        if cursor is not None:
            nxt = dict(cursor)
            nxt["mode"] = mode
            if mode == "confirm":
                current = displays.get(cursor["id"]) or "saved"
                nxt["current"] = current
                nxt["instructions"] = _confirm_instructions(cursor["id"], cursor["title"], current)
                nxt["links"] = []
                note = resources.get("database_note") if isinstance(resources, dict) else ""
                if cursor["id"] == "database" and note:
                    nxt["instructions"] = note + " " + nxt["instructions"]
        elif sanity and not sanity.get("ok"):
            failed = [item for item in sanity.get("checks") or [] if not item.get("ok")]
            opening = "These checks did not pass. " + ". ".join(
                item["label"] + ": " + item["detail"] for item in failed
            ) + "."
        elif sanity and sanity.get("ok"):
            opening = "The checks passed."
    can_start = bool(report["ready"]) and gate != "ask" and (sanity is None or sanity.get("ok") is True)
    if gate == "run" and nxt is not None:
        can_start = False
    described = dict(report)
    described.update({
        "gate": gate,
        "present": present,
        "missing": missing_titles,
        "opening": opening,
        "sanity": sanity,
        "can_start": can_start,
        "next": nxt,
    })
    return described


def _assignment_marks(assignments):
    fresh = set()
    just_used = set()
    for key, value in assignments:
        if key == "use":
            just_used.add(value.strip())
        step_id = _SETTING_STEP.get(key)
        if step_id:
            fresh.add(step_id)
    return fresh, just_used


def _publish_profile_now(report):
    nxt = report.get("next") or {}
    return report.get("gate") == "run" and nxt.get("id") == "profile" and nxt.get("mode") == "retrieve"


def _sync_profile_now(report, root, env, runtime_path):
    nxt = report.get("next") or {}
    if report.get("gate") != "run" or nxt.get("mode") != "retrieve":
        return False
    if nxt.get("id") in {"contact_email", "contact_phone"} and _contacts_need_sync(root, env):
        return True
    return nxt.get("id") == "calendly" and _calendly_needs_sync(root, runtime_path)


def run_onboard(args, opener=None, publisher=None, resources=None, sanity=None, schema=None):
    root = Path(args.root or project_root())
    env_path = root / ".env"
    runtime_path = root / "config" / "runtime.json"
    stage = (getattr(args, "stage", None) or "").strip()
    if stage and stage not in _step_ids():
        raise ValueError("unknown setup step: " + stage)
    assignments = []
    for item in args.set:
        if "=" not in item:
            raise ValueError("--set expects KEY=VALUE")
        key, value = item.split("=", 1)
        assignments.append((key.strip(), value.strip()))
    if assignments:
        apply_settings(assignments, root, runtime_path, env_path)
    env = merged_environment(env_path)
    if args.listen:
        from ajss.onboard.permissions import ENV_REFRESH, is_read_stage, listen as listen_read
        import urllib.request
        client_id = (env.get("GOOGLE_CLIENT_ID") or "").strip()
        client_secret = (env.get("GOOGLE_CLIENT_SECRET") or "").strip()
        chosen_opener = opener or urllib.request.urlopen
        if is_read_stage(stage):
            listen_read(stage, root, env_path, client_id, client_secret, chosen_opener)
            env = merged_environment(env_path, env)
            saved = parse_env_file(env_path)
            token_name = ENV_REFRESH[stage]
            if saved.get(token_name):
                env[token_name] = saved[token_name]
            accepted_step = stage
        else:
            if stage not in {"", "google"}:
                raise RuntimeError("This step does not listen for a Google authorization code")
            if (env.get("GOOGLE_REFRESH_TOKEN") or "").strip():
                raise RuntimeError("Google refresh token is already stored")
            refresh = listen_for_refresh_token(client_id, client_secret, chosen_opener)
            upsert_env(env_path, {"GOOGLE_REFRESH_TOKEN": refresh})
            env = merged_environment(env_path, env)
            env["GOOGLE_REFRESH_TOKEN"] = refresh
            accepted_step = "google"
        state = read_state(root) if _state_path(root).is_file() else {}
        _accept(state, accepted_step)
        write_state(root, state)
    if resources is None:
        resources = {}
    _warm_drive(env, runtime_path, resources)
    if not args.check and _database_is_current(env, root, runtime_path, stage):
        from ajss.onboard.database import ensure_queue_tables
        from ajss.settings import database_url
        _remember_database(root, resources, database_url(env), schema or ensure_queue_tables)
    fresh, just_used = _assignment_marks(assignments)

    def describe():
        return describe_wizard(
            assess(env, root, runtime_path),
            env, root, runtime_path, resources, sanity, just_used, fresh, stage,
        )

    report = describe()

    def publish(root, env, runtime_path):
        return publish_applicant_profile(root, env, runtime_path, resources=resources)

    chosen = publisher or publish
    if not args.check and _publish_profile_now(report):
        chosen(root, env, runtime_path)
        state = read_state(root) if _state_path(root).is_file() else {}
        _accept(state, "profile")
        write_state(root, state)
        report = describe()
    if not args.check and _sync_profile_now(report, root, env, runtime_path):
        chosen(root, env, runtime_path)
        report = describe()
    print(json.dumps(report, indent=2))
    if args.check and (not report["ready"] or (report.get("sanity") and not report["sanity"].get("ok"))):
        return 2
    return 0


def main(argv=None, opener=None):
    import argparse
    parser = argparse.ArgumentParser(description="Show the next local-run onboarding step")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--stage", default="", metavar="STEP")
    parser.add_argument("--listen", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", default=None)
    return run_onboard(parser.parse_args(argv), opener)
