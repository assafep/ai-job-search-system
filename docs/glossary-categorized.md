# Glossary — Categorized

> A comprehensive glossary of all terms and entities used in the AI Job Search System (AJSS), organized into three categories: **Software Engineering Definitions**, **Software Engineering Runtime Instances**, and **Application Domain Terms**.

---

## Software Engineering Definitions

Concepts, patterns, and abstractions that describe *how the system is designed*.

### Agent

A **stateless decision-maker** that receives serialized context and returns a schema-validated structured decision. Agents never hold adapter clients, persistence clients, or external-system access. They are invoked by the runtime and produce outcomes that the workload executor translates into persistence operations. Examples: `application`, `correspondence`, `interview-prep`, `read-posting`, `validate`, `applicant-profile`.

### Agent Provider

An abstraction layer that injects the LLM backend (e.g., Codex CLI, OpenAI, XAI, Grok) at the process boundary. The provider is configured via `AGENT_PROVIDER` and does not receive Drive, mail, calendar, browser, or persistence clients. It supplies the model invocation capability to stage runtimes.

### Agent Run

A single invocation of a stateless agent. The runtime passes context, the agent produces a structured decision, and the runtime consumes the result. An agent run has no side effects.

### Adapter

An integration module that connects the system to an external service (Google Drive, Gmail, Calendar, job boards, browser automation, LLM providers). Adapters normalize inputs and perform only the external action assigned to them. They emit normalized event contracts defined in `adapters/contracts.py`.

### Application Service

The **stable domain API** (defined in `openapi.yaml`) that owns all application-state and artifact mutations. It is the single persistence boundary: every write goes through it, and every write is explicit, validated, and idempotent where retries are possible. The current persistence implementation uses Google Drive (Google Docs journal), with PostgreSQL for completed applications and operational metadata.

### Approval Policy

A configuration-driven rule set (`config/automation-policy.yaml`) evaluated by `services.approval_service` to decide whether high-impact external actions should execute automatically, be deferred, or require human review. Prepared submissions are authorized by queue membership and bypass the approval gate. Missing policy entries default to `ask`.

### Approval Registry

An optional persistent store (`services.approval_registry`) for approval requests that require human review. Approval records are operational state; they do not themselves send email, submit applications, or modify Drive.

### Artifact

A file associated with an application, stored in Google Drive under `{Drive root}/Applications/Company / Role/`. Artifacts include cover letters, combined PDFs, job posting JSON files, and other generated documents. They are uploaded via the Application Service API with an idempotency key for retry safety.

### Boundary

A deliberate separation between components. Key boundaries in AJSS:
- **Persistence boundary**: The Application Service is the only component that mutates state.
- **Side-effect boundary**: `services.workload_executor` is the only stage-side logic that turns agent results into state mutations.
- **Agent boundary**: Agents never receive adapters or persistence clients.

### Claim-Execute-Settle Pattern

The lifecycle protocol for workload processing: a workload is **claimed** (transitioned from `queued` to `running`), **executed** (agents invoked, results validated, persistence operations applied), and **settled** (marked `succeeded`, `failed`, or `dead_letter`). This ensures at-least-once delivery with bounded retries.

### Declarative Stage Blueprint

A YAML file in `stages/` that declares a stage's name, trigger configuration, agent, input states, external actions, outcome routing, and side-effect policy. Blueprints are parsed by `services.stage_definitions` and consumed by the orchestrator and polling modules.

### Design Pattern: Stateless Decision Agent

Agents contain no internal state between invocations. All context is passed in, all decisions are returned. This enables replayability, testability, and provider-agnostic execution.

### Design Pattern: Idempotent Persistence

Every write operation carries an `idempotency_key`. For Google Docs, the key is retained as a small operation marker in the same event bullets and is removed from API responses, allowing restarted workloads to reconcile without duplicating journal events.

### Design Pattern: Persistence Boundary

All state mutations flow through a single API (the Application Service). Agents, adapters, and stage runtimes never mutate state directly. This keeps the persistence implementation replaceable and the domain contract stable.

### Design Pattern: Provider Injection

The LLM provider is injected at the process boundary via environment variable or configuration. The same agent prompts run behind different LLM SaaS providers while keeping Drive access in one replaceable persistence implementation.

### Design Pattern: Separation of Concerns (API / Adapter / Agent / Skill)

- **API** (`openapi.yaml`): Stable domain contract.
- **Adapter**: External system integration.
- **Agent**: Stateless decision logic.
- **Skill** / `AGENTS.md`: Deployment-specific operating guidance for an agent runtime.

### Dry-Run Mode

An execution mode where the workload executor plans persistence operations but does not execute them. Used for testing agent contracts and inspecting what operations would be applied without side effects.

### Event Dispatcher

A module (`services.event_dispatcher`) that creates workload envelopes from source events (calendar, email, scheduled polls) and enqueues them into the workload registry. Workload IDs are deterministic for a supplied source event ID, enabling reconciliation instead of duplicate workloads.

### Event Envelope

A structured container that carries found work to workers. Internal event envelopes are produced by the dispatcher; external messages and calendar changes are discovered by polling.

### Idempotency Key

A unique string attached to every write operation that prevents duplicate mutations on retry. Keys are derived from workload IDs, posting URLs, or content hashes.

### Installation

A configured instance of the system, identified by `config/runtime.json`. An installation has a journal (Google Docs), a Drive root, a service account, and an applicant profile.

### MCP (Model Context Protocol)

The protocol used to expose the Application Service to remote clients (notably ChatGPT). The MCP endpoint at `/mcp` supports Streamable HTTP with OAuth. MCP tools mirror the OpenAPI domain operations. For `validate-bookmarked` and `submit-prepared`, ChatGPT uses its native Browser/Computer Use through stage-specific MCP task tools.

### Onboarding

The initialization wizard (`python3 -m services.onboarding`) that collects journal, scheduling, and country settings, sets up service-account access to a Drive folder, creates `Applications` and `Applicant` folders, waits for a résumé PDF, and extracts the applicant profile.

### OpenAPI Contract

The file `openapi.yaml` — the authoritative request/response contract for the Application Service. MCP tool definitions are generated from it. Skills, prompts, and `AGENTS.md` files explain how to use it but are not the source of truth.

### Pipeline

A logical flow that spans the job-search lifecycle. A pipeline connects multiple stages. The orchestrator routes agent outcomes through pipelines defined in `config/pipelines.json`. Pipelines have names (e.g., `scout`, `preparation`, `auto-apply`), worker counts, and configured agents.

### Polling Schedule

A shared clock mechanism (`services.polling.PollingSchedule`) that enforces minimum intervals between polls. It uses monotonic time and admits work only after a worker accepts it. Scheduled stages use durable UTC minute-bucket IDs.

### Reasoning Effort

A per-stage configuration (`config/workload-effort.json`) that controls the LLM's reasoning depth (`low`, `medium`, `high`, `xhigh`). Reloaded each workload — no restart needed.

### Repository Backend

The pluggable storage implementation behind the Application Service. The default is Google Drive; `REPOSITORY_BACKEND=memory` provides an isolated in-memory backend for local development.

### Runtime Config

The module `services.runtime_config` that loads `config/runtime.json`. Every live stage entrypoint must load it before starting. Missing or incomplete configuration stops execution.

### Scout

A discovery worker that searches configured `discovery_sources` at a configured cadence. Scout does not choose the agent or the model; it uses the configured local coding agent (`CODING_AGENT` or `scout.coding_agent` in runtime config).

### Skill

A portable, deployable instruction file (e.g., `skills/application-service/SKILL.md`) that describes how an agent runtime should invoke the API and interpret contracts. It is not a database and does not replace the API implementation.

### Stage

A single processing step in a pipeline. A stage is defined by a declarative YAML blueprint and specifies its trigger, agent, input states, and outcome routing. Examples: `discover`, `bookmark-discovered`, `validate-bookmarked`, `prepare-validated`, `submit-prepared`, `prepare-interview`, `handle-incoming-email`, `reconcile`.

### Stage Task

A request to execute a stage. Stage tasks are durable records managed by the workload registry. A stage task is claimed, executed, and settled.

### State Machine

The finite-state transition model governing application lifecycle. Defined in `application_service.state_machine`, it enforces `ALLOWED_TRANSITIONS` between states. Invalid transitions are rejected.

### Stateless

A property of agents: they hold no internal state between invocations. All context is passed in as serialized data; all decisions are returned as structured output.

### Worker

A thread or process that executes stage tasks. Workers are allocated from a shared thread pool in the orchestrator. Worker counts are configured per pipeline and can be scaled live.

### Workload

A unit of work that the system processes. A workload instance gathers context, asks an agent for a structured decision, and applies the decision through the Application Service. Workloads are tracked in the `WorkloadRegistry`.

### Workload Executor

The module `services.workload_executor` — the side-effect boundary inside a workload. It validates agent results, produces explicit idempotent persistence operations, and can execute them through the client or return them unchanged in dry-run mode.

---

## Software Engineering Runtime Instances

Concrete classes, objects, and runtime entities that exist during execution.

### `ApplicationServiceClient`

The HTTP client (`services.application_service_client`) used by stage runtimes to communicate with the Application Service. It provides methods like `create_application`, `transition`, `append_event`, `store_artifact`, `get_application`, `list_applications`, `write_research`, `complete_application`, and `journal_tabs`.

### `ApplicationServiceError`

An exception raised by `ApplicationServiceClient` when the API returns an error (400, 404, 502, 503).

### `Orchestrator`

The central runtime class (`services.orchestrator.Orchestrator`) that manages continuous state queues with live consumer allocation. It holds a process lock, routes agent outcomes, manages a shared thread pool, and coordinates discovery, preparation, and submission pipelines.

### `PollingSchedule`

A runtime instance that tracks the last poll time for each stage name and enforces minimum intervals. Uses monotonic clock for accuracy across system time changes.

### `StageExecutor`

The class `services.stage_executor.StageExecutor` — the lifecycle boundary between queued workloads and stateless stage execution. It claims, executes, and settles one workload registry record. Methods: `prepare_context` (persistence reads), `run_agent` (stateless invocation), `run` (full claim-execute-settle).

### `WorkloadRecord`

A dataclass representing a single workload in the registry. Fields: `workload_id`, `stage`, `event`, `status`, `attempts`, `error`, `created_at`, `updated_at`, `result`.

### `WorkloadRegistry`

The class `services.workload_registry.WorkloadRegistry` — the core work-queuing mechanism of the system. It manages the lifecycle of all stage tasks from enqueue to completion, providing six states (`queued`, `running`, `succeeded`, `failed`, `dead_letter`, `awaiting_input`) and a bounded recovery loop. The registry ensures at-least-once delivery: on startup, `recover_incomplete` reclaims any workloads stuck in `running` state from a previous crash, transitioning them back to `queued` for retry. Key methods: `enqueue` (add new work), `claim` (transition to `running`), `complete` (mark `succeeded`), `fail` (mark `failed` for retry), `defer` (re-queue for later), `wait_for_input` (pause for external input like CAPTCHA), `reopen` (resume from paused state), `recover_incomplete` (crash recovery), `pending` (list pending work), and `report` (status summary). The registry is the bridge between the orchestrator's thread pool and individual `StageExecutor` instances — workers claim work from the registry and return results through it.

### `WorkloadRegistry` Statuses

The six lifecycle states of a workload record:
- **`queued`**: Waiting to be claimed.
- **`running`**: Currently being executed.
- **`succeeded`**: Completed successfully.
- **`failed`**: Failed but retryable.
- **`dead_letter`**: Failed and exceeded retry limit.
- **`awaiting_input`**: Paused waiting for external input (e.g., CAPTCHA, human approval).

### Persistence Operation

A dictionary describing a single state mutation. Types: `create_application`, `transition`, `append_event`, `store_posting_artifact`, `generate_and_store_artifact`, `ensure_ready_to_apply`, `complete_application`, `write_research`.

### Checkpoint

A saved intermediate result attached to a workload record. Checkpoints store the agent's output, artifact references, and effect-tracking flags (`effects_saved`, `completion_saved`) so that interrupted workloads can resume without re-invoking the model.

### Inflight

A dictionary inside the `Orchestrator` tracking currently running futures in the thread pool, mapped to their pipeline and application context.

### Thread Pool

A `ThreadPoolExecutor` inside the `Orchestrator` that manages concurrent workload execution. The pool size is configurable; the orchestrator admits work only when a thread is free.

### Discovery Budget

The maximum number of new postings a discovery poll can create, calculated as `max(0, 100 - tab_count)`. The budget is divided among available discovery workers.

### Provider Backoff

A runtime state (`provider_backoff_until`) that pauses pipelines affected by LLM provider quota exhaustion until the quota resets.

### Posting Identity

A normalized dictionary (`url`, `company`, `role`, `description`) used to recognize the same posting across different job boards and domains. Used for deduplication.

### Approval Outcome

The result of evaluating an action against the automation policy. Contains the action name, the decision (`auto`, `ask`, `defer`), and optionally a persisted approval request.

---

## Application Domain Terms

Concepts, entities, and states from the job search and recruitment domain.

### Application

A job application record representing a candidate's interest in a specific position at a specific company. It follows a lifecycle from discovery through submission to final outcome. Fields: `application_id`, `company`, `role`, `posting_url`, `status`, `created_at`, `updated_at`, `next_action`, `next_action_at`, `artifact_ids`, `interview_ids`, `last_event_id`.

### Application Package

The personalized set of documents prepared for a specific job application: a tailored cover letter combined with the applicant's résumé into a single PDF. Stored as an artifact in Drive.

### Applicant Profile

The structured representation of the job seeker, extracted from their résumé PDF. Stored at `Applicant/applicant_profile.json` in Drive. Contains personal info, professional experience, skills, employment history, education, certifications, and job search preferences (countries, search terms).

### ATS (Applicant Tracking System)

External HR software platforms (Greenhouse, Lever, Ashby, Workday, Rippling, ICIMS, etc.) that host job postings and manage applications. The system normalizes data from multiple ATS platforms.

### Auto-Apply

The automatic submission of prepared applications. Prepared queue membership authorizes automatic submission; no human approval pause. Failed auto-apply moves to `Manual Apply Required`.

### Bookmarked

An application state. The posting URL has been saved and queued for validation. The canonical bookmark stage only queues the link; ChatGPT validation opens the website to extract the role description.

### Calendar Event

A Google Calendar meeting that triggers the `prepare-interview` stage. The system polls Calendar for new or changed interview meetings and associates them with applications.

### Comeet

A specific ATS/job board platform. The system has a dedicated browser adapter (`adapters/browser/comeet.py`) and job board adapter (`adapters/job_boards/comeet.py`) for Comeet forms.

### Correspondence

Incoming recruitment emails matched to applications and classified by intent (rejection, offer, interview invitation, etc.). The `handle-incoming-email` stage processes correspondence.

### Dead Letters Queue

An application state for system errors (configuration, unexpected exceptions, invalid agent output, preparation failures). Dead letters are never consumed automatically; an operator must restore the appropriate source queue explicitly.

### Discovered

The initial application state. A posting was found by a discovery source (job board) and saved. Discovery counts all tabs in the main journal, including state and research tabs.

### Discovery Source

A job board or ATS platform that the system polls for new postings. Configured sources include: `freehire`, `jobicy`, `adzuna`, `greenhouse`, `ashby`, `comeet`, `lever`, `himalayas`, `smartrecruiters`.

### Duplicate

An application state for postings that match an already-handled position. When a posting moves to `Applied` or `Rejected`, matching items in `Discovered`, `Bookmarked`, `Validated`, `Prepared`, or `Manual Apply Required` are retired to `Duplicate`.

### Event Record

A dated entry in an application's journal. Each event has an `event_id`, `application_id`, `event_type`, `occurred_at`, and `data`. Events use the same format for manual and automated actions.

### Ghosting

The state of an application where the employer has not responded for an extended period. The `identify-ghosting` stage detects and marks ghosted applications.

### Interview Briefing

Research content generated for an upcoming interview. Includes company, product, role, interviewer, and engineering culture research. Stored as a nested Research tab in the journal.

### Interviewing

An application state indicating the candidate has one or more scheduled interviews.

### Invalidated

A completion reason applied when validation determines a posting is not a strong match for the applicant. The application tab is copied to the completed archive and removed from the main journal.

### Job Board

An external website or API that publishes job listings. The system integrates with multiple job boards through adapter modules.

### Job Posting

The raw or normalized content of a job listing, including company, role, description, location, and URL. Saved as a JSON artifact (`Job Posting - Company - Role.json`) during validation.

### Journal

The canonical application state store, implemented as a Google Docs document. Top-level tabs represent lifecycle states; each application is a child tab under its current state. Events are date paragraphs followed by bullet paragraphs.

### Journal Tab

A page within the Google Docs journal. Top-level tabs are state containers (e.g., `Discovered`, `Bookmarked`). Child tabs are individual applications. Nested tabs are research artifacts.

### Manual Apply Required

An application state for postings where automatic submission failed. The transition event includes the agent's failure explanation. Requires human intervention to complete the application.

### Offer

A terminal application state indicating the employer extended a job offer.

### Prepared

An application state indicating a personalized application package (cover letter + résumé PDF) has been generated and stored. Membership in this queue authorizes automatic submission.

### Posting URL Canonicalization

The process of normalizing posting URLs for deduplication: scheme/host casing, trailing slashes, fragments, and common tracking parameters are normalized so they do not create duplicate applications.

### Rejected

A terminal application state indicating the employer declined the application.

### Reconciliation

The process of rechecking missed events, recovering incomplete workloads, and reconciling application status. The `reconcile` stage runs on a scheduled poll.

### Research Tab

A nested child tab under an application tab, used to store interview briefings and other research artifacts. Not a separate application.

### Resume (Résumé)

The applicant's CV document, uploaded during onboarding. The original PDF is stored in Drive under `Applicant/`. It is used as the base for generating application packages.

### Scheduling Link

A Calendly (or similar) URL added to cover letters when the correspondence agent detects scheduling-related intents.

### Source Resume

The original résumé PDF file, referenced by `source_resume.path` in the applicant profile. Used as the base document for PDF generation.

### Status Transition

Moving an application from one lifecycle state to another. Transitions are validated against the state machine's `ALLOWED_TRANSITIONS` and recorded as dated journal events.

### Validated

An application state confirming a strong match between the role description and the candidate's experience. Validation occurs in ChatGPT's native browser, which reads the website and compares it with the applicant profile.

---

*End of glossary.*
