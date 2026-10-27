# AJSS storage — GDTQ and compatibility catalog

**Current GDTQ persistence** is intentionally simpler than the legacy Application Service:

| Data | System of record | Implementation |
| --- | --- | --- |
| Active application and nested research tabs | Google Docs | `gdtq.queue.GDTQ` |
| Append-only outcome events and freeform notes | Google Docs application tab | `GDTQ.push` and `application_events` |
| Candidate profile and resume | Google Drive `Applicant/` | `ApplicationContextLoader` |
| Posting content and submission artifacts | Google Drive `Applications/<Company - Role>/` | `gdtq.stages` / existing artifact store |
| Final outcome and complete history | Google Drive `Applications/` or `Failed/` | `CompletionPublisher` |
| URL/company-role dedup and round-robin/claim checkpoints | Persistent local SQLite | `gdtq.store.WorkIndex` |
| Legacy API, stage tasks and hosted coordination | PostgreSQL/Neon (compatibility only) | `application_service.database` |

No ChatGPT-hosted persistent key/value storage is assumed. Back up the SQLite volume and treat Drive/Docs operation recovery as a cross-system reconciliation task. The 100-tab Docs maximum includes all child research tabs; the new runtime uses a 90-tab soft budget.

---

## Historical Application Service backend reference

The following catalog describes the retained **legacy runtime**, not the GDTQ architecture. Some paths and ownership statements below were written for the old Application Service and are not normative for `gdtq/`.


This document catalogs every storage medium used by the AI Job Search System — the actual databases, cloud services, and local filesystem paths that running processes read from and write to. This is not about business data entities; it is about the physical and logical storage locations.

## Backends at a Glance

| Backend | Type | Used By |
| --- | --- | --- |
| **PostgreSQL** | Remote or local database server | Application Service, provisioning scripts |
| **Google Drive** | Cloud file storage | Application Service (via Repository interface) |
| **Google Docs** | Cloud document service | Application Service (journal tabs) |
| **Local filesystem — config/** | Project configuration files | All processes at startup |
| **Local filesystem — .onboarding/** | Provisioning state and secrets | Provisioning scripts only |
| **Local filesystem — logs/** | Activity and API logs | Application Service, orchestrator |
| **Local filesystem — /tmp/** | Temporary working directories | LLM adapters, browser adapter, Google auth |
| **Local filesystem — config/*.lock** | File-based locks | Pipeline scaling, provisioning |

---

## 1. PostgreSQL Database

**Type:** External database server (remote cloud instance or local Docker container)
**Connection:** Environment variable `AJSS_DATABASE_URL` (connection string)
**Accessed by:** Application Service (`application_service.database`), provisioning scripts (`provisioning/bootstrap/postgres.py`)

### Connection Details

- Connected via `psycopg` with `autocommit=True` and 15-second connect timeout.
- Uses PostgreSQL advisory locks (`pg_advisory_lock`) for coordination between concurrent instances.
- Statement timeout: 90 seconds, lock timeout: 30 seconds (set per session during locked operations).

### What It Stores

- **Relational tables:** `applications`, `events`, `artifacts`, `posting_keys`, `event_attributes`, `completed_snapshots`
- **Key-value JSONB store:** `installation_resources` (runtime coordination state: stage tasks, hosted workloads, hosted approvals)
- **SQL view:** `completed_postings` (deduplication lookup)

### Schema Files

- Base schema: `sql/schema.sql`
- Migrations: `sql/migrations/002_runtime.sql` through `005_stage_task_names.sql`

### Who Reads/Writes

| Process | Access |
| --- | --- |
| Application Service (HTTP API) | Read and write all tables |
| Application Service (MCP tools) | Read and write stage tasks |
| Provisioning scripts | Read and write during setup (schema creation, migrations) |
| Orchestrator workers | Read only (via Application Service API, not direct DB access) |

---

## 2. Google Drive

**Type:** Cloud file storage (user's personal Google Drive account)
**Access:** OAuth2 access token (via `GOOGLE_ACCESS_TOKEN`, `GOOGLE_REFRESH_TOKEN`, or service account)
**Accessed by:** Application Service only, through the `Repository` interface

### Folder Structure

All paths are relative to a configured Drive root folder (the "Interviews Journal" folder, identified by `google_drive.artifact_root_url` in `config/runtime.json`).

```
<Drive Root>/
├── Applicant/
│   └── applicant_profile.json          # Applicant profile (JSON)
├── .ai-job-search-system/
│   ├── stage_execution.json            # Workstation worker activation flag
│   └── workloads-internal-state/
│       └── <stage-name>/               # Per-stage private state files
│           ├── *.json                  # Internal state, checkpoints
│           └── *.pdf                   # Reports, generated documents
└── <Company> / <Role>/                 # Application artifacts (single folder, slash in name)
    ├── resume.pdf
    ├── cover-letter.pdf
    └── ...
```

### Key Paths (defined in `application_service/storage_layout.py`)

| Constant | Path | Purpose |
| --- | --- | --- |
| `SYSTEM_DIRECTORY` | `.ai-job-search-system/` | System-internal files |
| `APPLICANT_DIRECTORY` | `Applicant/` | Applicant data |
| `PROFILE_PATH` | `Applicant/applicant_profile.json` | Applicant profile JSON |
| `STAGE_EXECUTION_PATH` | `.ai-job-search-system/stage_execution.json` | Worker activation config |
| `WORKLOAD_STATE_DIRECTORY` | `.ai-job-search-system/workloads-internal-state/` | Stage-owned private state |

### Who Reads/Writes

| Process | Access |
| --- | --- |
| Application Service | Read and write all files and folders |
| Provisioning scripts | Create initial folder structure, upload profile |
| Orchestrator workers | Read only (via Application Service API) |
| Stage agents | No direct access — receive serialized context only |

### Hybrid Mode

When `AJSS_WORKLOAD_FILE_BACKEND=database` is set, files under `.ai-job-search-system/workloads-internal-state/` are stored in PostgreSQL (`installation_resources`) instead of Google Drive. The API remains identical.

---

## 3. Google Docs

**Type:** Cloud document service (single Google Docs document)
**Access:** Same OAuth2 credentials as Google Drive
**Accessed by:** Application Service only (`application_service.google_docs_repository`)

### The Interview Journal Document

- Identified by `google_drive.interview_journal_url` in `config/runtime.json` (a `docs.google.com/document/d/...` URL).
- Uses Google Docs' hierarchical tab system to represent application state.

### Tab Structure

```
<Interview Journal Document>
├── Bookmarked (state tab)
│   ├── Company A – Role 1 (application tab)
│   │   └── Research (optional research sub-tab)
│   └── Company B – Role 2 (application tab)
├── Prepared (state tab)
│   └── ...
├── Manual Apply Required (state tab)
├── Dead Letters Queue (state tab)
├── Applied (state tab)
├── Interviewing (state tab)
├── Rejected (state tab)
└── Offer (state tab)
```

- **State tabs** represent application lifecycle states (Bookmarked, Prepared, etc.).
- **Application tabs** are children of state tabs, one per application.
- **Research sub-tabs** are optional children of application tabs.

### Who Reads/Writes

| Process | Access |
| --- | --- |
| Application Service | Read and write tabs, content, and structure |
| Provisioning scripts | Create initial state tabs during onboarding |
| Orchestrator workers | Read only (via Application Service API) |

---

## 4. Local Filesystem — Configuration Files

**Type:** Project directory files (read at startup, occasionally written during provisioning)
**Location:** Project root (`/storage/k/Projects/ai-job-search-system/` or wherever the repo is cloned)

### config/runtime.json

- **Path:** `config/runtime.json` (relative to project root)
- **Purpose:** Primary runtime configuration — Google Drive URLs, logging settings, agent provider config, scheduling URLs.
- **Created by:** Onboarding command (`python3 -m services.onboarding`) or provisioning scripts.
- **Read by:** All processes at startup via `services.runtime_config.load()`.
- **Written by:** Provisioning scripts only. Never modified at runtime.
- **Override:** Environment variable `RUNTIME_CONFIG_PATH` can point to an alternative file.

### config/pipelines.json

- **Path:** `config/pipelines.json` (relative to project root)
- **Purpose:** Pipeline worker allocation and routing policy — which agents consume which queues, worker counts, state transition routes.
- **Created by:** Onboarding or provisioning.
- **Read by:** Orchestrator workers at startup and on scale events.
- **Written by:** `services.pipeline_config.scale()` — atomic write with file-based lock (`config/pipelines.json.lock`).
- **Lock file:** `config/pipelines.json.lock` — used by `fcntl.flock` to serialize pipeline scaling operations.

### .env

- **Path:** `.env` (project root)
- **Purpose:** Environment variable overrides (credentials, timeouts, API keys).
- **Created by:** Operator or provisioning scripts.
- **Read by:** Processes that source the file before startup.
- **Template:** `.env.example` provides the structure with blank values.

### stages/*.yaml

- **Path:** `stages/discover.yaml`, `stages/validate-bookmarked.yaml`, etc.
- **Purpose:** Stage blueprint definitions — agent prompts, schemas, effort levels.
- **Read by:** `services.stage_definitions` at runtime.
- **Written by:** Never at runtime — static configuration.

### schemas/*.json

- **Path:** `schemas/applicant-profile.schema.json`, etc.
- **Purpose:** JSON Schema files for validation.
- **Read by:** Application Service when validating profile data.
- **Written by:** Never at runtime — static configuration.

### agents/*/prompt.md

- **Path:** `agents/application/prompt.md`, `agents/read-posting/prompt.md`, etc.
- **Purpose:** Agent prompt templates.
- **Read by:** Stage executors when building agent context.
- **Written by:** Never at runtime — static configuration.

---

## 5. Local Filesystem — Provisioning State

**Type:** Private directory with mode 0o700 (owner-only access)
**Location:** `.onboarding/` (relative to project root)
**Accessed by:** Provisioning scripts only (`provisioning/`)

### Directory Structure

```
.onboarding/
├── lifecycle.json              # Provisioning checkpoint state
├── secrets.json                # Stored credentials (OAuth tokens, etc.)
├── provisioning.lock           # File-based lock for concurrent provisioning guard
└── *.pdf                       # Provisioning reports (PDF, human-readable)
```

### Files

| File | Purpose | Created By |
| --- | --- | --- |
| `lifecycle.json` | Tracks which provisioning steps have completed, with checkpoints per operation | `provisioning/state.State` |
| `secrets.json` | Stores OAuth tokens and other credentials with SHA-256 versioning | `provisioning/state.State` |
| `provisioning.lock` | Advisory lock file — prevents concurrent provisioning runs | `provisioning/state.State.locked()` |
| `*.pdf` | Human-readable provisioning reports | `provisioning/reporting.write_report()` |

### Atomic Write Pattern

All provisioning files use atomic write: create a temp file with prefix `.handoff-` or `.provision-report-`, write content, `fsync`, then `os.replace()` to the final path. This ensures no partial writes are visible.

### Who Reads/Writes

| Process | Access |
| --- | --- |
| Provisioning scripts | Read and write (single process enforced by lock) |
| Runtime processes | No access — provisioning state is not read at runtime |

---

## 6. Local Filesystem — Logs

**Type:** Append-only log files
**Location:** Configurable via `config/runtime.json` or environment variables

### Activity Log

- **Path:** Configured in `config/runtime.json` under `activity_log.path`, or via `ACTIVITY_LOG_FILE` environment variable.
- **Purpose:** Records application state transitions and worker activity (e.g., "Worker-1 (validate) started working on posting Acme / Backend").
- **Format:** Plain text, one line per event, appended with exclusive file lock (`fcntl.flock`).
- **Default:** Disabled — no file created unless explicitly enabled.
- **Written by:** Both the Application Service and the orchestrator (shared file).
- **Module:** `services.activity_log`

### API Log

- **Path:** Configured in `config/runtime.json` under `api_log.path`, or via `API_LOG_FILE` environment variable.
- **Purpose:** Records HTTP requests to the Application Service (Apache-style access log format).
- **Format:** Plain text, one line per request, appended with exclusive file lock.
- **Default:** Disabled — no file created unless explicitly enabled.
- **Written by:** Application Service HTTP handler only.
- **Module:** `services.api_log`

### Who Reads/Writes

| Process | Access |
| --- | --- |
| Application Service | Append to both logs (if enabled) |
| Orchestrator | Append to activity log only (if enabled) |
| Operator | Read only (for debugging/auditing) |

---

## 7. Local Filesystem — Temporary Directories

**Type:** OS-managed temp directory (typically `/tmp/` on Linux)
**Lifetime:** Short-lived — cleaned up when the creating process exits the context manager

### LLM Adapter Working Directories

| Adapter | Prefix | Contents | Module |
| --- | --- | --- | --- |
| Codex CLI | `job-search-codex-` | `schema.json`, `result.json` (structured output) | `adapters/llm/codex_provider.py` |
| Coding Agent (Grok/Codex) | `job-search-agent-` | `prompt.txt`, schema files, agent output | `adapters/llm/coding_agent.py` |

Each agent invocation creates a fresh temp directory, writes the prompt and schema, runs the agent subprocess, reads the result, and cleans up.

### Browser Adapter Working Directory

| Adapter | Prefix | Contents | Module |
| --- | --- | --- | --- |
| Comeet browser | `job-application-` | Downloaded application package file (PDF) | `adapters/browser/comeet.py` |

The browser adapter extracts the application package to a temp directory so the Chrome-based submission script can access the file path.

### Google Auth Service Account Key

| Purpose | Prefix | Contents | Module |
| --- | --- | --- | --- |
| Service account signing | `ajss-sa-` | PEM private key file (mode 0o600) | `application_service/google_auth.py` |

When the `cryptography` library is not available, the service account JWT signing falls back to OpenSSL CLI, which requires the private key in a temporary file.

### Who Reads/Writes

| Process | Access |
| --- | --- |
| LLM adapter subprocesses | Read prompt/schema, write result |
| Browser adapter (Node.js + Chrome) | Read package file |
| OpenSSL (fallback) | Read PEM key file |
| Python parent process | Creates, writes, reads, and deletes |

---

## 8. Local Filesystem — Lock Files

**Type:** Empty or near-empty files used with `fcntl.flock` for process coordination

| Lock File | Location | Protects | Module |
| --- | --- | --- | --- |
| `config/pipelines.json.lock` | Project root | Pipeline scaling (read-modify-write of pipelines.json) | `services.pipeline_config` |
| `.onboarding/provisioning.lock` | Project root | Provisioning state (prevents concurrent provisioning) | `provisioning.state` |

These are advisory locks — they coordinate between processes on the same machine. They do not protect against concurrent access from different machines (that is handled by PostgreSQL advisory locks).

---

## 9. Summary: Storage Access Matrix

Which process accesses which storage medium, and how:

| Storage Medium | Application Service | Orchestrator | Provisioning | LLM Adapters | Browser Adapter |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL | Direct SQL | Via API only | Direct SQL | — | — |
| Google Drive | Via Repository | Via API only | Create folders | — | — |
| Google Docs | Via Repository | Via API only | Create tabs | — | — |
| `config/runtime.json` | Read at startup | Read at startup | Write | Read (indirect) | — |
| `config/pipelines.json` | — | Read + atomic write | — | — | — |
| `.onboarding/` | — | — | Read + write | — | — |
| Log files | Append | Append | — | — | — |
| `/tmp/` working dirs | — | — | — | Create + delete | Create + delete |
| Lock files | — | Acquire | Acquire | — | — |

---

## 10. Environment Variables That Control Storage

| Variable | Controls | Default |
| --- | --- | --- |
| `AJSS_DATABASE_URL` | PostgreSQL connection string | Required, no default |
| `GOOGLE_ACCESS_TOKEN` | Static Google API token | Blank |
| `GOOGLE_REFRESH_TOKEN` | Refreshable Google API token | Blank |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service account JSON for Drive | Blank |
| `RUNTIME_CONFIG_PATH` | Override path for runtime.json | `config/runtime.json` |
| `AJSS_WORKLOAD_FILE_BACKEND` | Set to `database` for hybrid storage | Google Drive |
| `ACTIVITY_LOG_ENABLED` | Enable activity log | Disabled |
| `ACTIVITY_LOG_FILE` | Activity log file path | From runtime.json |
| `API_LOG_ENABLED` | Enable API request log | Disabled |
| `API_LOG_FILE` | API log file path | From runtime.json |
| `PERSISTENCE_API_URL` | Application Service HTTP endpoint | `http://127.0.0.1:8080` |
