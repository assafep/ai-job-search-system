# Job Search Automation

This project scaffolds an automated job-search system around four independent
workflows. The workflows are autonomous and event-driven, but their agents are
stateless and side-effect free. A workload instance gathers context, asks an
agent for a structured decision, and applies the decision through the data-layer
API.

## First run

Replay only the scout/bookmark stage for positions under the `Replay` tab with
`python3 -m services.replay_scout`. Supply `--application-id <tab_id>` to select
one position. The scout model opens each recorded URL, extracts the posting,
decides whether it fits the applicant, saves accepted postings, and prints
phase timings.
It does not run preparation or submission.
Use `python3 -m services.replay_scout --workers 7 --progress-seconds 10` for seven
Replay workers with each worker's progress printed every ten seconds. This bounded
pass snapshots Replay once, shares a synchronized persistence client and locked
registries, holds the queue-runner process lock, and publishes a PDF activity report.

The scout evaluates the source descriptions in `job_postings`. Bookmarking
stores the exact selected description in a `Job Posting - Company - Role.json`
artifact in the application folder and records its link in the position tab.
It does not download the employer page again. Preparation reads that saved
artifact through the persistence API; missing or mismatched source content
stops preparation rather than triggering another online fetch.

To process exactly one `Ready for Auto-Apply` position:

```bash
python3 -m services.ready_submission --application-id <application_id>
```

Ready queue membership authorizes automatic submission; there is no approval
flag or approval pause. For a bounded system run with per-workload activity and
a persisted report, run `python3 -m services.system_run`. Newly prepared
applications are submitted after they enter the ready queue. Email and interview
workflows poll read-only Gmail recruitment messages for active applications
(last 14 days) and primary-calendar interview events (next 30 days), then consume
matched events. Ambiguous matches are skipped and event IDs prevent duplicate
processing. Neither polling adapter sends email or changes calendar events.
Reports are stored under
`.ai-job-search-system/workloads-internal-state/system-run/` through the data API.
User-facing activity reports are human-readable PDFs under `system-run/reports/`.
JSON files are private machine checkpoints, not the delivered activity report.

Consume Bookmarked and Ready for Auto-Apply queues with exactly eight posting
threads: `python3 -m services.concurrent_queues`. Preparation successes continue
into submission in the same pool. Ready queue membership authorizes submission
automatically.
One shared persistence client serializes every storage read/write; registry and
approval operations use reentrant locks, and the persistence API itself handles
requests serially. Per-posting locks prevent duplicate work. A process lock
prevents two copies of this runner from sharing the operational JSON files.
Do not run a separate daemon/worker process against the same registries during
this run. The PDF report includes observed worker/storage concurrency counts.

The continuous orchestrator is `python3 -m services.orchestrator run`. It holds
the same process lock and routes each agent outcome from `config/pipelines.json`.
Change live capacity with `python3 -m services.orchestrator scale scout 2`;
`0` pauses that pipeline without stopping the others. While scout has workers,
it searches `discovery_sources` (Freehire and Jobicy by default) every
`discovery_seconds` and also once when the process starts. The job-discovery
agent chooses the matches, and each new match is saved as Bookmarked with its
posting text. A posting already in the journal is left where it is. Scout also
consumes `Replay`. Preparation consumes `Bookmarked`, and auto-apply consumes
`Ready for Auto-Apply`.
Scout and preparation send a duplicate of an Applied, Interviewing, Rejected,
or Offer posting to `Duplicate`. A match may be the same URL, the same company
and role, or the same posting text on another domain. Moving a posting to
Applied or Rejected also retires matching items still in Replay, Bookmarked,
Ready for Auto-Apply, or Manual Apply Required. The journal needs an optional
top-level `Duplicate` tab before those moves.

Activity lines are off unless `activity_log` in `config/runtime.json` sets
`enabled` and `path`, or the environment sets `ACTIVITY_LOG_ENABLED` and
`ACTIVITY_LOG_FILE`. The file records `Posting Company / Role moved from A to B`
for every persisted transition, plus `Worker-N (queue) started/finished working
on posting Company / Role` for each orchestrator worker. Transition lines are
written by the persistence API process, which reads this setting only at
startup. Restart that process after turning the log on. Worker lines are
written by the orchestrator process.

The persistence API writes nothing to the terminal. Its request log is a
separate file, off by default: `api_log` in `config/runtime.json`, or
`API_LOG_ENABLED` and `API_LOG_FILE`. The default path is
`logs/persistence-api.log`.

Start the persistence API and the pipelines in one process. That process accepts
the logging, monitor, and worker flags. Worker counts in `config/pipelines.json`
remain the defaults (all `0`, so a run with no `--workers` flags consumes nothing):

```bash
python3 -m services.run --monitor --log --log-file activity.log --workers scout=2 --workers preparation=4 --workers auto-apply=1
```

`--monitor` redraws each queue count in place once a second. `--log` and
`--no-log` turn the activity log on or off, and `--log-file` sets its path
(`--log-file` implies `--log`). Flags override `config/runtime.json` and the
environment. Stop an older `python3 -m persistence_api.main` before this
script. That process keeps the logging settings it loaded at startup.

`python3 -m services.queue_monitor` only redraws counts against an API that is
already listening. `python3 -m services.orchestrator monitor` does the same.
`python3 -m services.orchestrator run` starts pipelines only and accepts
`--monitor` and `--workers`.

Storage performance: journal reads share a five-second snapshot. Every journal
mutation starts from fresh state and invalidates the snapshot after its write.
State changes and their explanatory events use one atomic Google Docs batch.
Drive folder/file identities are cached for up to 60 seconds; file contents and
missing receipts are never cached. Report checkpoints upload after ten completed
postings or 60 seconds at a completion boundary, plus the initial and final
snapshots. Application state and submission receipts are still persisted before
dependent actions. The report includes request times, combined worker wait times,
and checkpoint counts. Tune the cache, checkpoint, and HTTP timeout values in
`.env.example` through the process environment; restart the API after code or
backend environment changes. A queue's reported finish precedes final PDF upload.

Successful saved packages move automatically to `Ready for Auto-Apply`, without
a human-review gate. To migrate prior review holds and reconsume every item in
the current Bookmarked queue, run
`python3 -m services.prepare_bookmarked_batch --migrate-review-holds`.
Saved packages are validated and reused without regenerating or deleting them;
new packages must be saved successfully before promotion. Each explicit batch
gets fresh workload identities without erasing previous workload history.
Preparation failures (posting retrieval, package validation, generation, or
storage) move the posting to `Dead Letters Queue` with an explanatory
transition event. They do not remain Bookmarked for automatic retry.

For an existing journal, explicitly migrate the lifecycle before starting:
`python3 -m services.journal_cli migrate-application-states --apply`.

Check these event sources without rerunning discovery/submission with
`python3 -m services.external_event_polling`. OAuth must grant
`https://www.googleapis.com/auth/gmail.readonly` and
`https://www.googleapis.com/auth/calendar.events.readonly` in addition to the
existing Drive/Docs scopes. Enable Gmail and Calendar APIs in the Google project.
Adding scopes to the consent configuration alone does not update an existing
refresh token: reauthorize the OAuth client and replace the refresh token in
the local secret environment. Do not put tokens in applicant_profile or Drive.
Polling reports blocked sources separately; unavailable sources never count as
successfully executed integrations. Bounded polls report `truncated` when more
source pages remain.
The default adapter uses local Chrome and Playwright for verified Comeet forms.
Run `npm install` and ensure `google-chrome` or `chromium` is on PATH (or set
`BROWSER_EXECUTABLE`). Employer destinations are in
`config/submission-destinations.json`; the adapter verifies the role link on
the employer's careers board before entering applicant data. Required unknown
answers stop submission. It uploads the unchanged combined PDF to both Resume
and Cover Letter controls. Configure `JOB_SUBMISSION_ADAPTER=module:callable`
to override this adapter with another browser integration.
It receives the verified application, profile, unmodified package bytes, and
the `submit-ready` reasoning preset. It returns an observed submission outcome,
confirmation text/reference, fields, attachment acceptance, and blockers.
Unsupported destinations report `Blocked` and move to `Manual Apply Required`.
Missing browser dependencies are system errors and move to `Dead Letters Queue`,
with an explanatory transition event.
Receipts are persisted through the data API before promotion to `Applied`.
Ordinary failed auto-apply attempts move the posting to `Manual Apply Required`;
system errors before transmission move it to `Dead Letters Queue`.
The transition records the failure explanation from the auto-apply agent.
The automatic scheduler does not consume that queue. Every Ready for Auto-Apply
item is submitted automatically. An ambiguous submission is never retried
automatically and requires manual outcome reconciliation.
An interrupted or unconfirmed attempt requires outcome reconciliation before
retrying, to avoid duplicate applications.

### Learned job-board routes

Verified Jobify, RVC Global and EchoJobs trial findings live in
`adapters/browser/playbooks/`; reusable UI actions are in `site_recipes.json`.
These are resumable operator-assisted browser adapters, not yet verified
end-to-end submission adapters. They are not automatically selected by the
submission daemon. Account/profile creation and external handoffs never count
as employer submission.

Start a trial with `BROWSER_HEADLESS=false node adapters/browser/trial_session.cjs jobify`
(or `rvc`, `echojobs`). The JSON-lines interface accepts `navigate`, `inspect`,
`click`, `fill`, `upload`, `check`, and `close`. A learned action can be invoked
as `{"op":"recipe","name":"open_application_handoff"}`. Registration recipes
also require `"allow_account_creation":true`; uploads require an absolute
`"path"` to the unchanged package. Read the site's playbook first. Persistent
browser sessions are protected, Git-ignored local state; never put their
cookies or tokens in Drive. Human/security checks must not be bypassed.

Observed progress is stored through the persistence API under
`submit-ready/site-trials/<site>/<application_id>.json`, separate from submission
receipts. Inspect it with `python3 -m services.site_trial_state --site jobify
--application-id <application_id>` (one command). Operator trial checkpoints
are recorded with `--stage` and `--evidence`; they cannot mark an application
as submitted or transition it to Applied. Resume from the checkpoint rather
than registering again or replaying a blocked route.

Reasoning effort for each workflow is configured in `config/workload-effort.json`.
All presets initially use `medium`. Change an entry to `low`, `medium`, `high`,
or `xhigh` (when supported by the selected model); omitted entries use `default`.
The Codex workload runtime reloads this file for each workload, so edits apply
to the next run without restarting. These presets override `CODEX_REASONING_EFFORT`
for workload runs; standalone provider calls still use that environment setting.
`reconcile` currently invokes no AI agent, so its effort setting has no effect.
Use `WORKLOAD_EFFORT_CONFIG_PATH` to load a different presets file.

Install the PDF rendering and assembly dependencies with
`python3 -m pip install -r requirements.txt`.
Application packages use a fixed styled cover-letter page followed by the
original onboarding resume PDF pages. Keep that source PDF accessible at the
profile's `source_resume.path`; the workload fails if it is unavailable and
never regenerates the resume from the structured profile. The output filename
is `<applicant_name> Cover Letter & Resume - <company_name> - <role_name>.pdf`.

The system expects a local runtime configuration at `config/runtime.json`.
Runtime configuration and the generated profile files are local state and are
ignored by Git; credentials are never stored in them.

For the default single-user setup, authenticate Codex CLI with your ChatGPT
account before running live agents:

```bash
codex --login
```

The default agent provider is `adapters.llm.codex_provider:provider`, which
uses that local Codex login and does not require `OPENAI_API_KEY`. Google Drive
still requires OAuth credentials in the environment: either
`GOOGLE_ACCESS_TOKEN`, or `GOOGLE_REFRESH_TOKEN` together with
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

If the file is absent, start onboarding:

```bash
python3 -m services.onboarding
```

Onboarding asks for:

1. the Google Drive connection/configuration;
2. the URL of the `Interviews Journal` Google Doc;
3. the URL of the Google Drive root folder for workflow artifacts;
4. an optional root-relative parent directory for application folders, such as
   `applications` (blank preserves the direct-root layout);
5. the Calendly URL used in application and recruitment correspondence;
6. the target countries as ISO codes, such as `IL,US`;
7. the path to the resume used to create `applicant_profile.json`.

Immediately after the resume is accepted, onboarding extracts contact details
and asks you to confirm your phone number. If one valid number is found, it is
shown as the default; press Enter to accept it or type a correction. If no
number is found (or several are found), you must enter one. The confirmed
number is validated and saved in international format as `personal.phone`,
including when onboarding syncs an existing Drive profile. Submission reads
this confirmed profile value.
For an existing setup missing a phone number, rerun
`python3 -m services.onboarding`; it runs only this missing confirmation step
and preserves the rest of the profile and runtime configuration.

The wizard creates a local `config/applicant_profile.json` alongside the runtime
configuration. It records the supplied resume and target countries as the
profile source for discovery. Search concepts are derived from the resume by
the discovery planner before adapters are queried; they are not manually
configured per adapter. Authentication
credentials remain outside the configuration file and are supplied by the
Google Drive adapter/runtime environment. When `GOOGLE_ACCESS_TOKEN` is already
configured, onboarding also creates the same profile in Drive if the root does
not already contain one; an existing Drive profile is retained. The configured
Calendly URL is stored in the profile's `application_defaults` as well as in
runtime configuration so cloud workloads can read it through `/profile`.
When the profile is inside the project, its runtime path is stored relatively
(`config/applicant_profile.json`) so the same configuration works in a mounted
container; a profile supplied from outside the project retains an absolute path.

The persistence API exposes the profile as `/profile` (`GET` and `PUT`). In the
Google Drive backend, it is stored at
`.ai-job-search-system/applicant_profile.json` below the configured Drive root.
Internal workload files use `.ai-job-search-system/workloads-internal-state/<workflow>/`.
Currently the submission workflow stores its receipts under
`submit-ready/submission-receipts/`; older duplicate receipts are retained in
`submit-ready/receipt-history/`. Workload folders are created only when needed.
The dot prefix is an organization convention; it does not hide files or change
Drive permissions. `POST /storage/migrate-internal` moves existing profile and
receipt files into this layout while preserving their file IDs and contents.
Local scheduler/approval registry files remain local operational bookkeeping.
Workflows also accept the legacy
`personal.calendly_url` location while preferring
`application_defaults.calendly_url`. Workflows should use this API rather than
reading or writing the Drive file directly.
Profile writes validate the required versioned envelope defined in
`schemas/applicant-profile.schema.json` before reaching a backend.

Binary artifacts use `POST /applications/{application_id}/artifacts` with a
filename, MIME type, and base64 content. The Google backend stores the file in
the application’s single `Company / Role` folder and records its Drive file ID
in the application journal. Callers should provide an `idempotency_key` for
retryable uploads.

Artifacts can be listed with `GET /applications/{application_id}/artifacts` and
read as base64 with the corresponding artifact-id endpoint. This keeps file
reads inside the same persistence boundary as file writes.

Workflows may also use the generic root-relative file endpoints (`POST
/files` and `GET /files?path=...`) for artifacts that are not owned by one
application. Paths are relative to the configured Drive root and are validated
against traversal or absolute-path escapes.

Interview preparation uses `POST /applications/{application_id}/research` to
create a nested research tab under the application. The workload also appends
the dated briefing event to the application journal, so the tab and event stay
linked.
Existing nested research is available through the corresponding `GET` endpoint,
so recovery and preparation workloads can inspect prior briefings without
accessing Google Docs directly.

Workload state changes are explicit: a detected interview moves the application
to `Interviewing`; correspondence classified as rejection or offer moves it to
`Rejected` or `Offer`; scheduling correspondence moves it to `Interviewing`.
For scheduling or rescheduling intents, a configured `calendly_url` is
deterministically included in the proposed response draft.
Discovery candidates become idempotent application-creation operations in the
`Bookmarked` state; package preparation advances each prepared application to
`Ready for Auto-Apply`, which authorizes automatic submission. The application
moves to `Applied` only when the workload receives explicit confirmation from
the external submission step.

## Workflows

### A. Job discovery and application

Trigger: scheduled, every few hours or daily.

```text
discover jobs
  → normalize postings
  → deduplicate
  → score against preferences
  → personalize application
  → generate PDF
  → submit automatically from the ready queue
  → track result
```

Entering the `Ready for Auto-Apply` queue authorizes the system to submit the
application automatically.

### B. Interview preparation

Trigger: a new or changed interview meeting.

```text
detect meeting
  → associate with application
  → research company, product, role, interviewer, and engineering culture
  → create interview briefing
  → track preparation status
```

### C. Recruitment correspondence

Trigger: incoming recruitment email.

```text
match email thread
  → classify intent
  → update application state or prepare response
  → send according to approval policy
```

### D. Reconciliation and recovery

Trigger: independent daily schedule.

```text
recheck missed events
  → recover incomplete workloads
  → reconcile application status
  → renew subscriptions
  → report errors and pending review requests for actions configured to ask
```

## Data layer

The data layer is the only component allowed to mutate persistent workflow
state. Agents and workflow instances use the API defined in `openapi.yaml`.
Google Drive is a persistence implementation, not an agent-facing API.

### Job-posting/application states

The supported lifecycle states are:

```text
Bookmarked → Ready for Auto-Apply → Applied → Interviewing → Offer
                    ↓
          Manual Apply Required → Applied (after verified manual submission)
                         └──────────────────────→ Rejected
```

`Rejected` and `Offer` are terminal for the current application attempt. The
implementation may later add explicit `Withdrawn` or `Skipped` states, but they
are not part of this initial documented lifecycle.

### Persistence representation

The Interview Journal is the canonical application state store:

- top-level tabs are the seven states listed above;
- each application is a child tab under the state that currently contains it;
- an application tab is identified by its Google Docs tab ID;
- the tab title is the human-readable company / role label;
- each event is a date paragraph followed by one or more bullet paragraphs;
- manual and automated events use the same journal format;
- URLs written by the persistence API are native Google Docs hyperlinks;
- nested Research tabs remain children of the application tab and are treated as
  research artifacts, not separate applications;
- moving an application to another state updates the parent tab and appends a
  dated status-change event;
- artifact references are appended to the application journal after files are
  saved under one single-level folder named `Company / Role`. By default it is
  directly below the configured Drive root; `application_root_path` may place
  all such folders below a root-relative parent such as `applications/`.
  Company and role are not nested folders.
- posting URLs are canonicalized for deduplication: scheme/host casing,
  trailing slashes, fragments, and common tracking parameters do not create a
  second application.

Use `GET /journal/status` to validate the seven required state containers. A
missing container is reported rather than silently treated as an empty state;
repair is an explicit `POST /journal/repair` operation through the API.

Retryable writes carry an idempotency key. For Google Docs, the key is retained
as a small operation marker in the same event bullets and is removed from API
responses, allowing a restarted workload to reconcile without duplicating a
journal event.

## Architecture

```text
event source
  → event dispatcher
    → workflow runtime
    → stateless agent
      → structured decision
        → data-layer API
          → Google Drive / Docs and other adapters
```

The API contract is portable. Skills, prompts, and `AGENTS.md` files explain
how a particular agent runtime should use it; they are not the source of truth.
The deployable [`skills/persistence-api/SKILL.md`](skills/persistence-api/SKILL.md)
contains the operational boundary and usage rules for runtimes that support
skills. The OpenAPI contract remains authoritative for request and response
shapes.
### Runtime flow

Workflow runtimes should use `services.persistence_client.PersistenceApiClient`
for reads and writes. This keeps the Google Drive implementation behind the
HTTP persistence boundary and prevents agents from acquiring external-system
access.

During discovery and application execution:

- The runtime passes discovery results between stateless agents as serialized
  context.
- Package writes go through the persistence API.
- Agents never hold a Drive client.

### Workload execution

`services.workload_executor` is the side-effect boundary inside a workload. It
validates agent results, produces explicit idempotent persistence operations,
and can execute them through the client or return them unchanged in dry-run
mode.

It also handles workflow-specific artifact preparation:

- When an application context includes `cv_html`, it plans a combined PDF
  artifact operation.
- Only execution mode sends generated bytes to the persistence API.
- When `calendly_url` is present, the document generator adds that scheduling
  link to the cover letter exactly once.

### Workload identity and recovery

Dispatcher workload IDs are deterministic for a supplied source event ID, so a
replayed calendar, email, or scheduled event can be reconciled instead of
creating a second logical workload.

`services.workload_registry.WorkloadRegistry` provides the corresponding
queued/running/failed/succeeded lifecycle and bounded recovery loop for the
reconciliation workflow.

When a reconciliation workload is claimed:

- Interrupted workloads return to `queued` until their retry limit is reached.
- An application reconciliation result can append a dated
  `reconciliation_completed` event and apply an explicitly supplied target
  status through the persistence API.
- The runtime returns registry counts and queued, failed, or dead-letter
  records requiring attention.
- When approval persistence is enabled, the result includes pending approval
  counts and records.

### API, adapter, agent, and skill boundaries

- The persistence API is the stable domain contract in `openapi.yaml`; it owns
  application-state and artifact mutations.
- An adapter/tool is an integration with an external system such as Google
  Drive, Gmail, Calendar, a job board, or a browser. Adapters normalize inputs
  and perform only the external action assigned to them.
- An agent is a stateless decision-maker. It receives serialized context and
  returns a schema-validated decision; it never receives an adapter or
  persistence client.
- A skill or `AGENTS.md` file is deployment-specific operating guidance for an
  agent runtime. It describes how to invoke the API and interpret contracts,
  but it is not a database and does not replace the API implementation.

This separation lets the same agent prompts run behind different LLM SaaS
providers while keeping Drive access in one replaceable persistence
implementation.

The repository includes provider-neutral adapter contracts and normalization
seams. Concrete Gmail, Calendar, job-board, browser, and LLM-provider clients
remain deployment-specific integrations; they should emit the normalized event
contracts in `adapters/contracts.py` and invoke the runtime/API boundaries
described above.

## Testing individual agents and workloads

The repository includes a side-effect-free contract runner. It lets you test a
specific agent or workload with fixture data before connecting credentials or
external adapters.

```bash
python3 -m services.test_runner list

python3 -m services.test_runner agent interview-prep \
  --fixture tests/fixtures/agents/interview-prep/input.json \
  --output tests/fixtures/agents/interview-prep/output.json

python3 -m services.test_runner workload prepare-interview \
  --fixture tests/fixtures/workloads/prepare-interview.json

python3 -m services.workload_runner prepare-interview \
  --fixture tests/fixtures/workloads/prepare-interview.json
```

Equivalent shortcuts are available through `make test`, `make list`,
`make dry-run-interview`, `make dry-run-discovery`,
`make dry-run-correspondence`, and `make dry-run-reconcile`.
Run `make verify` for the complete local contract, compilation, dry-run, and
Compose configuration check.

Inspect the configured journal through the persistence API with
`python3 -m services.journal_cli status`. A missing state tab can be created
only through the explicit `python3 -m services.journal_cli repair --apply`
command; duplicate tabs are reported for manual review.

The runner validates agent output schemas and produces dry-run workload plans;
it never creates external side effects. The workload plan also shows which
agents would be invoked and which persistence actions are proposed. See
`tests/README.md`.

`--execute` is an explicit opt-in for applying persistence operations. It
requires complete results for every agent in the selected workflow, completed
onboarding, and a running persistence API; ordinary test
drives remain side-effect free. If execute mode is used before onboarding, the
runner stops without side effects and prints the onboarding command.

For queued workload execution, use `python3 -m services.workflow_runtime` with
`--workload-id` and an agent-results JSON file. This is the scheduler/container
entrypoint; it claims and settles one durable workload record. The equivalent
shortcut is `make run-workload WORKLOAD_ID=work_... AGENT_RESULTS=/path/results.json`.

For continuous processing, configure an LLM provider as
`AGENT_PROVIDER=package.module:provider` (optional; Codex CLI is the default
for preparation, auto-apply, correspondence, and interview). Job discovery,
including Replay scout and choosing matches from job-board listings, runs the
local coding agent named by `CODING_AGENT` or `scout.coding_agent` in
`config/runtime.json` (environment settings take precedence). Scout does not choose the agent or
the model. Set `CODING_AGENT_MODEL` only to override the agent's own default.
The launcher does not send `XAI_API_KEY`. `DISCOVERY_PROVIDER` can replace it
with another provider module. Then run
`python3 -m services.worker_main --poll-seconds 30` (or add `--once` for a
single drain; `--limit N` bounds a one-shot drain). The provider is injected at the process boundary; it does not
receive Drive, mail, calendar, browser, or persistence clients.

Unexpected exceptions, invalid agent outputs, configuration errors, and artifact
storage failures route to `Dead Letters Queue`, retaining the failure explanation.
Ordinary application blockers go to `Manual Apply Required`; an unconfirmed
submission always needs manual reconciliation. Dead letters are never consumed
automatically. Restore the appropriate source queue explicitly after fixing the
cause. For an existing journal, restart the persistence API with the new code and
run `python3 -m services.journal_cli migrate-application-states --apply` to add the
dead-letter tab without changing existing positions or history.

For a local scheduled worker, set a durable registry path before starting the
worker so queued workloads survive process restarts:

```bash
export WORKLOAD_REGISTRY_PATH="$PWD/config/workloads.json"
source .env
python3 -m services.worker_main --poll-seconds 30
```

High-impact external actions are evaluated by `services.approval_service` against
`config/automation-policy.yaml`. Ready for Auto-Apply submissions are authorized
by queue membership and do not create approval requests. Other missing policy
entries default to `ask`. Dry-run mode never executes actions, regardless of the
policy decision.
Requests requiring review can be persisted with `services.approval_registry`
using `APPROVAL_REGISTRY_PATH`; approval records remain operational state and
do not themselves send email, submit applications, or modify Drive.
Normalized workload payloads may declare external actions in
`approval_actions`; the runtime records policy decisions and durable `ask`
requests before an external adapter is allowed to act.

Every live workflow entrypoint must load `services.runtime_config` before
starting. Missing or incomplete configuration stops execution and directs the
operator to onboarding. `services.workflow_runtime` is the current one-workload
entrypoint; it can consume captured agent results or invoke agents through an
injected provider.

The persistence API can be run locally or with Docker Compose:

```bash
docker compose up --build persistence-api
```

Set `PERSISTENCE_API_TOKEN` in deployments to require bearer authentication on
all state endpoints; `/health` remains the public liveness probe and `/ready`
is the public persistence-readiness probe. If unset, the API is intentionally
unauthenticated for local development.

When `config/runtime.json` exists, a local API started without an explicit
backend selection automatically uses the configured Google Drive repository.
Set `REPOSITORY_BACKEND=memory` when you intentionally want an isolated local
backend.

## Repository layout

```text
config/       local and policy configuration
agents/       stateless agent instructions and output schemas
workflows/    event-triggered workflow definitions
adapters/     Google, Gmail, Calendar, browser, LLM integrations
services/     API, onboarding, approvals, generation, dispatch
templates/    cover-letter, CV, and briefing templates
schemas/      shared domain schemas
skills/       portable runtime instructions for the persistence API
tests/        contract and workflow tests
deployment/   deployment manifests and container materials
```
