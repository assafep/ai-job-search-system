# Persistence Entities Catalog

> **Compatibility note:** The model below documents legacy Application Service entities and PostgreSQL mappings. GDTQ stores active applications in Google Docs, with SQLite identity/checkpoint indexes and Drive JSON archives; see [GDTQ](../gdtq/README.md).


This document catalogs every persistence storage entity in the AI Job Search System, organized by backend. Each entity shows its structure, which API endpoints access it, which code modules own it, and what scope of the application may read or write it.

## Backends at a Glance

| Backend | Role | Access Method |
| --- | --- | --- |
| **PostgreSQL** | Relational data, runtime coordination, completed archives | Direct SQL via `application_service.database` |
| **Google Drive** | Application journal (Docs), artifacts, profile, stage-owned state | `application_service.repository.Repository` interface |
| **Google Docs** | Application journal tabs (embedded in Drive backend) | `application_service.google_docs_repository` |

---

## 1. PostgreSQL

All tables live in the `public` schema. Two table types serve different purposes:
- **Relational tables** (`applications`, `events`, `artifacts`, `posting_keys`, `event_attributes`) store structured application data.
- **`installation_resources`** is a key-value JSONB store for runtime coordination state.

### 1.1. `applications`

**Type:** Relational table
**Defined in:** `sql/schema.sql`

| Column | Type | Constraints |
| --- | --- | --- |
| `application_id` | text | PRIMARY KEY |
| `company` | text | NOT NULL |
| `role` | text | NOT NULL |
| `posting_url` | text | NOT NULL, UNIQUE |
| `status` | text | NOT NULL, DEFAULT 'Bookmarked' |
| `created_at` | timestamptz | NOT NULL, DEFAULT CURRENT_TIMESTAMP |
| `updated_at` | timestamptz | NOT NULL, DEFAULT CURRENT_TIMESTAMP |
| `next_action` | text | nullable |
| `next_action_at` | timestamptz | nullable |
| `source_document_id` | text | nullable |
| `source_record_id` | text | nullable |
| `heading` | text | nullable (added by migration 003) |
| `terminal_status` | text | nullable (added by migration 003) |
| `completed_on` | date | nullable (added by migration 003) |

**Unique constraint:** `(source_document_id, source_record_id)`
**Index:** `applications_status_idx` on `status`

**API endpoints:**
- `GET /applications` → `listApplications`
- `POST /applications` → `createApplication`
- `GET /applications/{id}` → `getApplication`
- `PATCH /applications/{id}` → `transitionApplication`
- `DELETE /applications/{id}` → `deleteApplication`
- `GET /journal/terminal-items` → `listTerminalJournalItems`
- `POST /journal/complete-terminal` → `completeTerminalJournal`
- `POST /bulk-updates` → `bulkUpdateItems`

**Owned by:** `application_service.repository.Repository`, `application_service.google_docs_repository`
**Scope:** Readable by workload runtimes and operators via API. Writable only through the Application Service (state machine validated transitions). Stage agents cannot write directly.

### 1.2. `events`

**Type:** Relational table
**Defined in:** `sql/schema.sql`

| Column | Type | Constraints |
| --- | --- | --- |
| `event_id` | text | PRIMARY KEY |
| `application_id` | text | NOT NULL, FK → applications |
| `event_type` | text | NOT NULL |
| `occurred_at` | timestamptz | NOT NULL, DEFAULT CURRENT_TIMESTAMP |
| `occurred_on` | date | nullable (added by migration 003) |
| `position` | integer | nullable (added by migration 003) |
| `body` | text | nullable (added by migration 003) |
| `source_document_id` | text | nullable (added by migration 003) |
| `data` | jsonb | NOT NULL, DEFAULT '{}' |

**Index:** `events_application_idx` on `(application_id, occurred_at, event_id)`

**API endpoints:**
- `GET /applications/{id}/events` → `listApplicationEvents`
- `POST /applications/{id}/events` → `appendApplicationEvent`

**Owned by:** `application_service.repository.Repository`
**Scope:** Readable by workload runtimes (context for agent decisions). Writable only through the Application Service. Events are append-only journal entries.

### 1.3. `artifacts`

**Type:** Relational table
**Defined in:** `sql/schema.sql`

| Column | Type | Constraints |
| --- | --- | --- |
| `artifact_id` | text | PRIMARY KEY |
| `application_id` | text | NOT NULL, FK → applications |
| `name` | text | NOT NULL |
| `drive_file_id` | text | nullable (reference to Google Drive file) |
| `mime_type` | text | nullable |
| `created_at` | timestamptz | NOT NULL, DEFAULT CURRENT_TIMESTAMP |

**Index:** `artifacts_application_idx` on `application_id`

**API endpoints:**
- `GET /applications/{id}/artifacts` → `listApplicationArtifacts`
- `POST /applications/{id}/artifacts` → `storeApplicationArtifact`
- `GET /applications/{id}/artifacts/{artifactId}` → `readApplicationArtifact`

**Owned by:** `application_service.repository.Repository`, `application_service.google_drive_artifacts`
**Scope:** Metadata is stored in PostgreSQL; actual file content lives in Google Drive. Readable by workload runtimes. Writable only through the Application Service after a Drive upload succeeds.

### 1.4. `posting_keys`

**Type:** Relational table
**Defined in:** `sql/migrations/003_completed_postings.sql`

| Column | Type | Constraints |
| --- | --- | --- |
| `application_id` | text | NOT NULL, FK → applications |
| `key_type` | text | NOT NULL, CHECK IN ('url', 'company_role') |
| `lookup_key` | text | NOT NULL |
| `raw_value` | text | NOT NULL |

**Primary key:** `(key_type, lookup_key)`
**Index:** `posting_keys_application_idx` on `application_id`

**API endpoints:** No direct API endpoint. Used internally for deduplication.

**Owned by:** `application_service.repository.Repository` (created during application creation/transition)
**Scope:** Internal deduplication index. Multiple job-board URLs can map to one application. The `company_role` key ensures different boards still count as already applied.

### 1.5. `event_attributes`

**Type:** Relational table
**Defined in:** `sql/migrations/003_completed_postings.sql`

| Column | Type | Constraints |
| --- | --- | --- |
| `event_id` | text | NOT NULL, FK → events |
| `name` | text | NOT NULL |
| `value` | text | NOT NULL |

**Primary key:** `(event_id, name, value)`

**API endpoints:** No direct API endpoint. Populated during event processing.

**Owned by:** `application_service.repository.Repository`
**Scope:** Internal parsed attributes extracted from event data for querying.

### 1.6. `completed_postings` (View)

**Type:** SQL view
**Defined in:** `sql/migrations/003_completed_postings.sql`

Joins `posting_keys` and `applications` to answer "was this posting already processed to completion?" Filters on `completed_on IS NOT NULL` OR `status IN ('Rejected', 'Invalidated')`.

**Columns:** `key_type`, `lookup_key`, `raw_value`, `application_id`, `company`, `role`, `terminal_status`, `completed_on`, `status`

**API endpoints:** No direct API endpoint. Used internally during discovery to skip already-completed postings.

**Owned by:** `application_service.repository.Repository`
**Scope:** Read-only deduplication lookup used by the discovery stage.

### 1.7. `installation_resources`

**Type:** Key-value JSONB store
**Defined in:** `sql/migrations/002_runtime.sql`

| Column | Type | Constraints |
| --- | --- | --- |
| `resource_key` | text | PRIMARY KEY |
| `data` | jsonb | NOT NULL |

This single table stores all runtime coordination state as JSONB values. The following keys are used:

#### Key: `stage_tasks`

**Purpose:** Durable stage task requests, claims, and progress.

**Structure:** JSONB object keyed by `stage_task_id`. Each record contains:
- `stage_task_id` (string) — unique identifier
- `stage` (string) — stage name
- `request` (object) — original request (`stage`, `application_id`, `limit`)
- `application_ids` (array) — selected application IDs for queue polls
- `execution_host` (string) — `'workstation'` or `'chatgpt'`
- `blueprint_digest` (string) — SHA-256 of stage definition at admission time
- `status` (string) — `queued`, `running`, `succeeded`, `failed`, `reconciliation_required`
- `created_at`, `updated_at`, `started_at`, `finished_at` (timestamps)
- `worker_id` (string, optional) — workstation worker identifier
- `claim_token` (string, optional) — worker claim token
- `chatgpt` (object, optional) — per-posting native-browser task state
- `report` (object, optional) — PDF report reference
- `summary` (object, optional) — stage execution summary
- `reconciliation_note` (string, optional)

**API endpoints:**
- `GET /stages` → `listStages`
- `POST /stage-tasks` → `startStageTask`
- `GET /stage-tasks` → `listStageTasks`
- `GET /stage-tasks/{id}` → `getStageTask`
- `GET /stage-tasks/{id}/report` → `getStageTaskReport`
- `POST /stage-tasks/{id}/reconcile` → `reconcileStageTask`
- `POST /stage-tasks/claim` → `claimStageTask`
- `POST /stage-tasks/{id}/worker-update` → `updateStageTask`
- MCP: `nextChatGPTStageTask`, `completeChatGPTValidation`, `beginChatGPTSubmission`, `completeChatGPTSubmission`, `pauseChatGPTStageTask`, `getChatGPTSubmissionPackage`

**Owned by:** `application_service.stage_tasks.StageTasks`
**Scope:** Admitted by the Application Service API (ChatGPT or operator). Consumed by workstation workers (`claim`) or ChatGPT native browser (`nextChatGPTStageTask`). Monitored via read-only list endpoint.

#### Key: `hosted_workloads`

**Purpose:** Persistent workload registry for hosted execution (legacy/local mode).

**Structure:** JSONB object keyed by workload ID. Each record is a `WorkloadRecord` with fields: `workload_id`, `stage`, `application_id`, `status`, `created_at`, `updated_at`, and result data.

**API endpoints:** No direct API endpoint. Accessed through the legacy workload executor.

**Owned by:** `application_service.hosted_registry.HostedWorkloads` (extends `services.workload_registry.WorkloadRegistry`)
**Scope:** Internal runtime state. Used by the local workstation worker for the legacy workload registry lifecycle (queued → running → succeeded/failed/dead-letter/awaiting_input).

#### Key: `hosted_approvals`

**Purpose:** Persistent approval request registry.

**Structure:** JSONB object with two top-level keys:
- `records` — object keyed by approval ID, each an `ApprovalRequest`
- `idempotency` — object for idempotency tracking

**API endpoints:** No direct API endpoint.

**Owned by:** `application_service.hosted_registry.HostedApprovals` (extends `services.approval_registry.ApprovalRegistry`)
**Scope:** Internal runtime state. Note: the `Prepared` queue does **not** use approval requests — prepared items are submitted automatically.

### 1.8. `completed_snapshots`

**Type:** Relational table
**Defined in:** `sql/migrations/002_runtime.sql`

| Column | Type | Constraints |
| --- | --- | --- |
| `application_id` | text | PRIMARY KEY, FK → applications |
| `snapshot` | jsonb | NOT NULL |

**Purpose:** JSON snapshot of completed applications for archival.

**API endpoints:** No direct API endpoint.

**Owned by:** `application_service.completion`
**Scope:** Internal archive. Created when an application reaches a terminal state.

---

## 2. Google Drive

Google Drive serves as the backend for the application journal, artifacts, profile, and stage-owned internal state. All access goes through the `Repository` interface — never directly via a Drive client.

### 2.1. Application Journal (Google Docs)

**Type:** Google Docs document with hierarchical tabs
**Path:** Configured Drive root (the "Interviews Journal")

**Structure:**
- One top-level tab per application state: `Bookmarked`, `Prepared`, `Manual Apply Required`, `Dead Letters Queue`, `Applied`, `Interviewing`, `Rejected`, `Offer`
- Under each state tab: one child tab per application (title = "Company – Role")
- Under each application tab: optional research sub-tabs

**API endpoints:**
- `GET /journal/queues` → `journalQueueCounts`
- `GET /journal/tabs` → `listJournalTabs`
- `GET /journal/status` → `getJournalStatus`
- `POST /journal/repair` → `repairJournalStateTabs`
- `POST /journal/migrate-application-states` → `migrateApplicationStates`
- `GET /applications` → `listApplications`
- `POST /applications` → `createApplication`
- `GET /applications/{id}` → `getApplication`
- `PATCH /applications/{id}` → `transitionApplication`
- `GET /applications/{id}/events` → `listApplicationEvents`
- `POST /applications/{id}/events` → `appendApplicationEvent`
- `POST /applications/{id}/research` → `writeApplicationResearch`
- `GET /applications/{id}/research` → `listApplicationResearch`

**Owned by:** `application_service.google_docs_repository`
**Scope:** This is the authoritative store for in-progress application state. Workload runtimes read it via the Application Service API. Stage agents receive serialized context but never access it directly.

### 2.2. Applicant Profile

**Type:** JSON file
**Path:** `Applicant/applicant_profile.json`

**Structure:** JSON object validated against `schemas/applicant-profile.schema.json`. Contains applicant's personal information, skills, experience, and preferences.

**API endpoints:**
- `GET /profile` → `getApplicantProfile`
- `PUT /profile` → `saveApplicantProfile`

**Owned by:** `application_service.repository.Repository`, `application_service.profile`
**Scope:** Readable by workload runtimes (provided to agents as context). Writable by operators via API. Validated on save.

### 2.3. Stage Execution Config

**Type:** JSON file
**Path:** `.ai-job-search-system/stage_execution.json`

**Structure:** `{'workstation_enabled': bool}`

**API endpoints:** No direct API endpoint. Read internally by stage execution readiness checks.

**Owned by:** `application_service.repository.Repository`
**Scope:** Internal configuration controlling workstation worker activation.

### 2.4. Stage-Owned Internal State

**Type:** Files (JSON, PDF, etc.)
**Path:** `.ai-job-search-system/workloads-internal-state/<stage>/<filename>`

**Structure:** Per-stage directory for workload-owned internal files. Path construction uses `application_service.storage_layout.workload_state_path(stage, filename)`.

**API endpoints:**
- `GET /files/{path}` → `readRootRelativeFile`
- `POST /files/{path}` → `storeRootRelativeFile`

**Owned by:** `application_service.repository.Repository`
**Scope:** Stage-owned private state. Paths are path-traversal-protected. In hybrid installations configured with `AJSS_WORKLOAD_FILE_BACKEND=database`, these files are stored in PostgreSQL instead of Drive, but the same `/files` API is used.

### 2.5. Application Artifacts (Drive Files)

**Type:** Files (PDF, DOCX, etc.)
**Path:** `Company / Role/` (single-level folder under configured Drive root)

**Structure:** Generated application packages (résumé, cover letters, etc.) stored as Drive files. Metadata reference is recorded in PostgreSQL `artifacts` table.

**API endpoints:**
- `GET /applications/{id}/artifacts` → `listApplicationArtifacts`
- `POST /applications/{id}/artifacts` → `storeApplicationArtifact`
- `GET /applications/{id}/artifacts/{artifactId}` → `readApplicationArtifact`

**Owned by:** `application_service.repository.Repository`, `application_service.google_drive_artifacts`
**Scope:** Created during the preparation stage. Readable by workload runtimes for submission. Note: folder structure is `Company / Role` as a single folder name with a slash delimiter, not nested folders.

### 2.6. Research Tabs

**Type:** Google Docs tabs (children of application tabs)
**Path:** Under each application's journal tab

**Structure:** Nested tabs containing research notes and findings about a specific application.

**API endpoints:**
- `GET /applications/{id}/research` → `listApplicationResearch`
- `POST /applications/{id}/research` → `writeApplicationResearch`

**Owned by:** `application_service.google_docs_repository`
**Scope:** Readable and writable by workload runtimes via API. Research tabs are not applications — they are notes attached to an application.

---

## 3. Hybrid Storage (Database-Backed Files)

When `AJSS_WORKLOAD_FILE_BACKEND=database` is configured, stage-owned internal state files that would normally live in Google Drive are stored in PostgreSQL instead.

### 3.1. Database File Storage

**Type:** PostgreSQL `installation_resources` (special keys)
**Trigger:** Environment variable `AJSS_WORKLOAD_FILE_BACKEND=database`

**Behavior:** Files stored via `/files` API for paths under `.ai-job-search-system/workloads-internal-state/` are persisted in PostgreSQL rather than Google Drive. Legacy Drive files remain readable. The API is identical.

**API endpoints:** Same `/files` endpoints — the backend is transparent to callers.

**Owned by:** `application_service.hybrid_repository`
**Scope:** Same as stage-owned internal state, but backed by PostgreSQL for installations without Drive access for internal files.

---

## 4. Access Scope Summary

| Entity | Stage Agents | Workload Runtimes | ChatGPT Native Browser | Operators (API) |
| --- | --- | --- | --- | --- |
| `applications` | Read (via context) | Read/Write (via API) | Read (via MCP) | Read/Write |
| `events` | Read (via context) | Read/Write (via API) | Read (via MCP) | Read |
| `artifacts` | Read (via context) | Read/Write (via API) | Read (via MCP) | Read |
| `posting_keys` | — | Internal | — | — |
| `completed_postings` | — | Read (dedup) | — | — |
| `installation_resources.stage_tasks` | — | Read (monitor) | Read/Write (MCP) | Read/Write |
| `installation_resources.hosted_workloads` | — | Read/Write | — | — |
| `installation_resources.hosted_approvals` | — | Read/Write | — | — |
| `completed_snapshots` | — | Internal | — | — |
| Journal (Google Docs) | Read (via context) | Read/Write (via API) | Read (via MCP) | Read |
| Profile | Read (via context) | Read/Write (via API) | Read (via MCP) | Read/Write |
| Stage execution config | — | Read | — | — |
| Stage-owned state files | — | Read/Write | — | — |
| Application artifacts (Drive) | Read (via context) | Read/Write (via API) | Read (via MCP) | Read |
| Research tabs | Read (via context) | Read/Write (via API) | — | Read |

**Key rules:**
- **Stage agents** are stateless decision-makers. They receive serialized context and return structured results. They never call the API, Drive, Docs, or any persistence layer directly.
- **Workload runtimes** use `ApplicationServiceClient` (or equivalent) to access the Application Service API.
- **ChatGPT native browser** accesses state only through MCP tools for `validate-bookmarked` and `submit-prepared` stages.
- **Operators** use the HTTP API directly or via MCP.
- All persistent mutations must be explicit, validated, and retry-safe via idempotency keys.

---

## 5. Migration History

| Migration | File | Changes |
| --- | --- | --- |
| Initial | `sql/schema.sql` | `applications`, `events`, `artifacts` tables |
| 002 | `sql/migrations/002_runtime.sql` | `installation_resources` (key-value store), `completed_snapshots` |
| 003 | `sql/migrations/003_completed_postings.sql` | `posting_keys`, `event_attributes`, `completed_postings` view; added columns to `applications` and `events` |
| 004 | `sql/migrations/004_stage_identity.sql` | Added `stage` field to `workflow_runs` records (data-only) |
| 005 | `sql/migrations/005_stage_task_names.sql` | Renamed `workflow_runs` → `stage_tasks`, renamed fields (`workflow` → `stage`, `run_id` → `stage_task_id`) across all runtime keys |

---

## 6. Locking

PostgreSQL advisory locks coordinate concurrent access to `installation_resources`:

| Lock Name | Protects |
| --- | --- |
| `stage-tasks` | `stage_tasks` admission and claims |
| `stage-execution` | Stage execution state and ChatGPT task sessions |
| `workflow-execution` | Legacy lock name (still acquired during migration 005 cutover) |

The `installation_lock` context manager in `application_service.database` acquires these locks before mutating runtime state.
