# Glossary — Learning Progression

> A glossary ordered for someone reading from beginning to end to build a mental model of the entire system. Each term is tagged with its category: **[SE Definition]**, **[SE Runtime]**, or **[Domain]**.

---

## Part 1 — The Big Picture

### AI Job Search System (AJSS) [Domain]

An automated job-search system that polls job boards, validates postings against a candidate's profile, prepares personalized application packages, and submits applications — all orchestrated through autonomous, time-driven stages.

### Application [Domain]

The central domain entity: a job application record representing a candidate's interest in a specific position at a specific company. It follows a lifecycle from discovery through submission to final outcome. Fields: `application_id`, `company`, `role`, `posting_url`, `status`, `created_at`, `updated_at`, `next_action`, `next_action_at`, `artifact_ids`, `interview_ids`, `last_event_id`.

### Application Lifecycle [Domain]

The sequence of states an application passes through:

```
Discovered → Bookmarked → Validated → Prepared → Applied → Interviewing → Offer / Rejected
```

Failed auto-apply moves to `Manual Apply Required`; system errors move to `Dead Letters Queue`.

### Job Posting [Domain]

The raw or normalized content of a job listing, including company, role, description, location, and URL. The starting point of every application.

### Job Board [Domain]

An external website or API that publishes job listings. The system integrates with multiple job boards (Freehire, Jobicy, Adzuna, Greenhouse, Ashby, Comeet, Lever, Himalayas, SmartRecruiters) through adapter modules.

### ATS (Applicant Tracking System) [Domain]

External HR software platforms (Greenhouse, Lever, Ashby, Workday, Rippling, ICIMS, etc.) that host job postings and manage applications. The system normalizes data from multiple ATS platforms.

---

## Part 2 — How the System Is Organized

### Pipeline [SE Definition]

A logical flow that spans the job-search lifecycle. A pipeline connects multiple stages. The orchestrator routes agent outcomes through pipelines defined in `config/pipelines.json`. Pipelines have names (e.g., `scout`, `preparation`, `auto-apply`), worker counts, and configured agents.

### Stage [SE Definition]

A single processing step in a pipeline. A stage is defined by a declarative YAML blueprint and specifies its trigger, agent, input states, and outcome routing. Examples: `discover`, `bookmark-discovered`, `validate-bookmarked`, `prepare-validated`, `submit-prepared`, `prepare-interview`, `handle-incoming-email`, `reconcile`.

### Declarative Stage Blueprint [SE Definition]

A YAML file in `stages/` that declares a stage's name, trigger configuration, agent, input states, external actions, outcome routing, and side-effect policy. Blueprints are parsed by `services.stage_definitions` and consumed by the orchestrator and polling modules.

### Stage Task [SE Definition]

A request to execute a stage. Stage tasks are durable records managed by the workload registry. A stage task is claimed, executed, and settled.

### Workload [SE Definition]

A unit of work that the system processes. A workload instance gathers context, asks an agent for a structured decision, and applies the decision through the Application Service. Workloads are tracked in the `WorkloadRegistry`.

### Agent [SE Definition]

A **stateless decision-maker** that receives serialized context and returns a schema-validated structured decision. Agents never hold adapter clients, persistence clients, or external-system access. They are invoked by the runtime and produce outcomes that the workload executor translates into persistence operations. Examples: `application`, `correspondence`, `interview-prep`, `read-posting`, `validate`, `applicant-profile`.

### Stateless [SE Definition]

A property of agents: they hold no internal state between invocations. All context is passed in as serialized data; all decisions are returned as structured output. This enables replayability, testability, and provider-agnostic execution.

### Agent Run [SE Definition]

A single invocation of a stateless agent. The runtime passes context, the agent produces a structured decision, and the runtime consumes the result. An agent run has no side effects.

### Agent Provider [SE Definition]

An abstraction layer that injects the LLM backend (e.g., Codex CLI, OpenAI, XAI, Grok) at the process boundary. The provider is configured via `AGENT_PROVIDER` and does not receive Drive, mail, calendar, browser, or persistence clients.

### Adapter [SE Definition]

An integration module that connects the system to an external service (Google Drive, Gmail, Calendar, job boards, browser automation, LLM providers). Adapters normalize inputs and perform only the external action assigned to them.

### Application Service [SE Definition]

The **stable domain API** (defined in `openapi.yaml`) that owns all application-state and artifact mutations. It is the single persistence boundary: every write goes through it, and every write is explicit, validated, and idempotent where retries are possible.

### OpenAPI Contract [SE Definition]

The file `openapi.yaml` — the authoritative request/response contract for the Application Service. MCP tool definitions are generated from it. Skills, prompts, and `AGENTS.md` files explain how to use it but are not the source of truth.

### Skill [SE Definition]

A portable, deployable instruction file (e.g., `skills/application-service/SKILL.md`) that describes how an agent runtime should invoke the API and interpret contracts. It is not a database and does not replace the API implementation.

### Design Pattern: Separation of Concerns (API / Adapter / Agent / Skill) [SE Definition]

- **API** (`openapi.yaml`): Stable domain contract.
- **Adapter**: External system integration.
- **Agent**: Stateless decision logic.
- **Skill** / `AGENTS.md`: Deployment-specific operating guidance for an agent runtime.

### Boundary [SE Definition]

A deliberate separation between components. Key boundaries in AJSS:
- **Persistence boundary**: The Application Service is the only component that mutates state.
- **Side-effect boundary**: `services.workload_executor` is the only stage-side logic that turns agent results into state mutations.
- **Agent boundary**: Agents never receive adapters or persistence clients.

### Design Pattern: Persistence Boundary [SE Definition]

All state mutations flow through a single API (the Application Service). Agents, adapters, and stage runtimes never mutate state directly. This keeps the persistence implementation replaceable and the domain contract stable.

### Design Pattern: Stateless Decision Agent [SE Definition]

Agents contain no internal state between invocations. All context is passed in, all decisions are returned. This enables replayability, testability, and provider-agnostic execution.

### Design Pattern: Provider Injection [SE Definition]

The LLM provider is injected at the process boundary via environment variable or configuration. The same agent prompts run behind different LLM SaaS providers while keeping Drive access in one replaceable persistence implementation.

---

## Part 3 — How Work Gets Done

### Worker [SE Definition]

A thread or process that executes stage tasks. Workers are allocated from a shared thread pool in the orchestrator. Worker counts are configured per pipeline and can be scaled live.

### `Orchestrator` [SE Runtime]

The central runtime class (`services.orchestrator.Orchestrator`) that manages continuous state queues with live consumer allocation. It holds a process lock, routes agent outcomes, manages a shared thread pool, and coordinates discovery, preparation, and submission pipelines.

### Thread Pool [SE Runtime]

A `ThreadPoolExecutor` inside the `Orchestrator` that manages concurrent workload execution. The pool size is configurable; the orchestrator admits work only when a thread is free.

### Inflight [SE Runtime]

A dictionary inside the `Orchestrator` tracking currently running futures in the thread pool, mapped to their pipeline and application context.

### `WorkloadRegistry` [SE Runtime]

The class `services.workload_registry.WorkloadRegistry` — the core work-queuing mechanism of the system. It manages the lifecycle of all stage tasks from enqueue to completion, providing six states (`queued`, `running`, `succeeded`, `failed`, `dead_letter`, `awaiting_input`) and a bounded recovery loop. The registry ensures at-least-once delivery: on startup, `recover_incomplete` reclaims any workloads stuck in `running` state from a previous crash, transitioning them back to `queued` for retry. Key methods: `enqueue` (add new work), `claim` (transition to `running`), `complete` (mark `succeeded`), `fail` (mark `failed` for retry), `defer` (re-queue for later), `wait_for_input` (pause for external input like CAPTCHA), `reopen` (resume from paused state), `recover_incomplete` (crash recovery), `pending` (list pending work), and `report` (status summary). The registry is the bridge between the orchestrator's thread pool and individual `StageExecutor` instances — workers claim work from the registry and return results through it.

### `WorkloadRecord` [SE Runtime]

A dataclass representing a single workload in the registry. Fields: `workload_id`, `stage`, `event`, `status`, `attempts`, `error`, `created_at`, `updated_at`, `result`.

### `WorkloadRegistry` Statuses [SE Runtime]

The six lifecycle states of a workload record:
- **`queued`**: Waiting to be claimed.
- **`running`**: Currently being executed.
- **`succeeded`**: Completed successfully.
- **`failed`**: Failed but retryable.
- **`dead_letter`**: Failed and exceeded retry limit.
- **`awaiting_input`**: Paused waiting for external input (e.g., CAPTCHA, human approval).

### Claim-Execute-Settle Pattern [SE Definition]

The lifecycle protocol for workload processing: a workload is **claimed** (transitioned from `queued` to `running`), **executed** (agents invoked, results validated, persistence operations applied), and **settled** (marked `succeeded`, `failed`, or `dead_letter`). This ensures at-least-once delivery with bounded retries.

### `StageExecutor` [SE Runtime]

The class `services.stage_executor.StageExecutor` — the lifecycle boundary between queued workloads and stateless stage execution. It claims, executes, and settles one workload registry record. Methods: `prepare_context` (persistence reads), `run_agent` (stateless invocation), `run` (full claim-execute-settle).

### Workload Executor [SE Definition]

The module `services.workload_executor` — the side-effect boundary inside a workload. It validates agent results, produces explicit idempotent persistence operations, and can execute them through the client or return them unchanged in dry-run mode.

### Persistence Operation [SE Runtime]

A dictionary describing a single state mutation. Types: `create_application`, `transition`, `append_event`, `store_posting_artifact`, `generate_and_store_artifact`, `ensure_ready_to_apply`, `complete_application`, `write_research`.

### `ApplicationServiceClient` [SE Runtime]

The HTTP client (`services.application_service_client`) used by stage runtimes to communicate with the Application Service. It provides methods like `create_application`, `transition`, `append_event`, `store_artifact`, `get_application`, `list_applications`, `write_research`, `complete_application`, and `journal_tabs`.

### `ApplicationServiceError` [SE Runtime]

An exception raised by `ApplicationServiceClient` when the API returns an error (400, 404, 502, 503).

### Checkpoint [SE Runtime]

A saved intermediate result attached to a workload record. Checkpoints store the agent's output, artifact references, and effect-tracking flags (`effects_saved`, `completion_saved`) so that interrupted workloads can resume without re-invoking the model.

### Event Dispatcher [SE Definition]

A module (`services.event_dispatcher`) that creates workload envelopes from source events (calendar, email, scheduled polls) and enqueues them into the workload registry. Workload IDs are deterministic for a supplied source event ID, enabling reconciliation instead of duplicate workloads.

### Event Envelope [SE Definition]

A structured container that carries found work to workers. Internal event envelopes are produced by the dispatcher; external messages and calendar changes are discovered by polling.

---

## Part 4 — Scheduling and Polling

### Polling Schedule [SE Definition]

A shared clock mechanism (`services.polling.PollingSchedule`) that enforces minimum intervals between polls. It uses monotonic time and admits work only after a worker accepts it. Scheduled stages use durable UTC minute-bucket IDs.

### `PollingSchedule` [SE Runtime]

A runtime instance that tracks the last poll time for each stage name and enforces minimum intervals. Uses monotonic clock for accuracy across system time changes.

### Scout [SE Definition]

A discovery worker that searches configured `discovery_sources` at a configured cadence. Scout does not choose the agent or the model; it uses the configured local coding agent (`CODING_AGENT` or `scout.coding_agent` in runtime config).

### Discovery Source [Domain]

A job board or ATS platform that the system polls for new postings. Configured sources include: `freehire`, `jobicy`, `adzuna`, `greenhouse`, `ashby`, `comeet`, `lever`, `himalayas`, `smartrecruiters`.

### Discovery Budget [SE Runtime]

The maximum number of new postings a discovery poll can create, calculated as `max(0, 100 - tab_count)`. The budget is divided among available discovery workers.

### Provider Backoff [SE Runtime]

A runtime state (`provider_backoff_until`) that pauses pipelines affected by LLM provider quota exhaustion until the quota resets.

### Posting Identity [SE Runtime]

A normalized dictionary (`url`, `company`, `role`, `description`) used to recognize the same posting across different job boards and domains. Used for deduplication.

---

## Part 5 — Reliability and Safety

### Design Pattern: Idempotent Persistence [SE Definition]

Every write operation carries an `idempotency_key`. For Google Docs, the key is retained as a small operation marker in the same event bullets and is removed from API responses, allowing restarted workloads to reconcile without duplicating journal events.

### Idempotency Key [SE Definition]

A unique string attached to every write operation that prevents duplicate mutations on retry. Keys are derived from workload IDs, posting URLs, or content hashes.

### Dry-Run Mode [SE Definition]

An execution mode where the workload executor plans persistence operations but does not execute them. Used for testing agent contracts and inspecting what operations would be applied without side effects.

### Approval Policy [SE Definition]

A configuration-driven rule set (`config/automation-policy.yaml`) evaluated by `services.approval_service` to decide whether high-impact external actions should execute automatically, be deferred, or require human review. Prepared submissions are authorized by queue membership and bypass the approval gate. Missing policy entries default to `ask`.

### Approval Registry [SE Definition]

An optional persistent store (`services.approval_registry`) for approval requests that require human review. Approval records are operational state; they do not themselves send email, submit applications, or modify Drive.

### Approval Outcome [SE Runtime]

The result of evaluating an action against the automation policy. Contains the action name, the decision (`auto`, `ask`, `defer`), and optionally a persisted approval request.

### Reasoning Effort [SE Definition]

A per-stage configuration (`config/workload-effort.json`) that controls the LLM's reasoning depth (`low`, `medium`, `high`, `xhigh`). Reloaded each workload — no restart needed.

---

## Part 6 — The Application Domain Lifecycle

### Discovered [Domain]

The initial application state. A posting was found by a discovery source (job board) and saved. Discovery counts all tabs in the main journal, including state and research tabs.

### Bookmarked [Domain]

An application state. The posting URL has been saved and queued for validation. The canonical bookmark stage only queues the link; ChatGPT validation opens the website to extract the role description.

### Validated [Domain]

An application state confirming a strong match between the role description and the candidate's experience. Validation occurs in ChatGPT's native browser, which reads the website and compares it with the applicant profile.

### Invalidated [Domain]

A completion reason applied when validation determines a posting is not a strong match for the applicant. The application tab is copied to the completed archive and removed from the main journal.

### Prepared [Domain]

An application state indicating a personalized application package (cover letter + résumé PDF) has been generated and stored. Membership in this queue authorizes automatic submission.

### Application Package [Domain]

The personalized set of documents prepared for a specific job application: a tailored cover letter combined with the applicant's résumé into a single PDF. Stored as an artifact in Drive.

### Auto-Apply [Domain]

The automatic submission of prepared applications. Prepared queue membership authorizes automatic submission; no human approval pause. Failed auto-apply moves to `Manual Apply Required`.

### Applied [Domain]

An application state indicating the application was successfully submitted.

### Manual Apply Required [Domain]

An application state for postings where automatic submission failed. The transition event includes the agent's failure explanation. Requires human intervention to complete the application.

### Interviewing [Domain]

An application state indicating the candidate has one or more scheduled interviews.

### Offer [Domain]

A terminal application state indicating the employer extended a job offer.

### Rejected [Domain]

A terminal application state indicating the employer declined the application.

### Dead Letters Queue [Domain]

An application state for system errors (configuration, unexpected exceptions, invalid agent output, preparation failures). Dead letters are never consumed automatically; an operator must restore the appropriate source queue explicitly.

### Duplicate [Domain]

An application state for postings that match an already-handled position. When a posting moves to `Applied` or `Rejected`, matching items in `Discovered`, `Bookmarked`, `Validated`, `Prepared`, or `Manual Apply Required` are retired to `Duplicate`.

### Ghosting [Domain]

The state of an application where the employer has not responded for an extended period. The `identify-ghosting` stage detects and marks ghosted applications.

### Status Transition [Domain]

Moving an application from one lifecycle state to another. Transitions are validated against the state machine's `ALLOWED_TRANSITIONS` and recorded as dated journal events.

### State Machine [SE Definition]

The finite-state transition model governing application lifecycle. Defined in `application_service.state_machine`, it enforces `ALLOWED_TRANSITIONS` between states. Invalid transitions are rejected.

---

## Part 7 — Persistence and Storage

### Journal [Domain]

The canonical application state store, implemented as a Google Docs document. Top-level tabs represent lifecycle states; each application is a child tab under its current state. Events are date paragraphs followed by bullet paragraphs.

### Journal Tab [Domain]

A page within the Google Docs journal. Top-level tabs are state containers (e.g., `Discovered`, `Bookmarked`). Child tabs are individual applications. Nested tabs are research artifacts.

### Event Record [Domain]

A dated entry in an application's journal. Each event has an `event_id`, `application_id`, `event_type`, `occurred_at`, and `data`. Events use the same format for manual and automated actions.

### Artifact [SE Definition]

A file associated with an application, stored in Google Drive under `{Drive root}/Applications/Company / Role/`. Artifacts include cover letters, combined PDFs, job posting JSON files, and other generated documents. They are uploaded via the Application Service API with an idempotency key for retry safety.

### Research Tab [Domain]

A nested child tab under an application tab, used to store interview briefings and other research artifacts. Not a separate application.

### Repository Backend [SE Definition]

The pluggable storage implementation behind the Application Service. The default is Google Drive; `REPOSITORY_BACKEND=memory` provides an isolated in-memory backend for local development.

### Posting URL Canonicalization [Domain]

The process of normalizing posting URLs for deduplication: scheme/host casing, trailing slashes, fragments, and common tracking parameters are normalized so they do not create duplicate applications.

---

## Part 8 — The Applicant

### Applicant Profile [Domain]

The structured representation of the job seeker, extracted from their résumé PDF. Stored at `Applicant/applicant_profile.json` in Drive. Contains personal info, professional experience, skills, employment history, education, certifications, and job search preferences (countries, search terms).

### Resume (Résumé) [Domain]

The applicant's CV document, uploaded during onboarding. The original PDF is stored in Drive under `Applicant/`. It is used as the base for generating application packages.

### Source Resume [Domain]

The original résumé PDF file, referenced by `source_resume.path` in the applicant profile. Used as the base document for PDF generation.

### Interview Briefing [Domain]

Research content generated for an upcoming interview. Includes company, product, role, interviewer, and engineering culture research. Stored as a nested Research tab in the journal.

### Calendar Event [Domain]

A Google Calendar meeting that triggers the `prepare-interview` stage. The system polls Calendar for new or changed interview meetings and associates them with applications.

### Scheduling Link [Domain]

A Calendly (or similar) URL added to cover letters when the correspondence agent detects scheduling-related intents.

---

## Part 9 — Correspondence and External Events

### Correspondence [Domain]

Incoming recruitment emails matched to applications and classified by intent (rejection, offer, interview invitation, etc.). The `handle-incoming-email` stage processes correspondence.

### Reconciliation [Domain]

The process of rechecking missed events, recovering incomplete workloads, and reconciling application status. The `reconcile` stage runs on a scheduled poll.

---

## Part 10 — Configuration and Deployment

### Installation [SE Definition]

A configured instance of the system, identified by `config/runtime.json`. An installation has a journal (Google Docs), a Drive root, a service account, and an applicant profile.

### Onboarding [SE Definition]

The initialization wizard (`python3 -m services.onboarding`) that collects journal, scheduling, and country settings, sets up service-account access to a Drive folder, creates `Applications` and `Applicant` folders, waits for a résumé PDF, and extracts the applicant profile.

### Runtime Config [SE Definition]

The module `services.runtime_config` that loads `config/runtime.json`. Every live stage entrypoint must load it before starting. Missing or incomplete configuration stops execution.

### MCP (Model Context Protocol) [SE Definition]

The protocol used to expose the Application Service to remote clients (notably ChatGPT). The MCP endpoint at `/mcp` supports Streamable HTTP with OAuth. MCP tools mirror the OpenAPI domain operations. For `validate-bookmarked` and `submit-prepared`, ChatGPT uses its native Browser/Computer Use through stage-specific MCP task tools.

### Comeet [Domain]

A specific ATS/job board platform. The system has a dedicated browser adapter (`adapters/browser/comeet.py`) and job board adapter (`adapters/job_boards/comeet.py`) for Comeet forms.

---

*End of glossary.*
