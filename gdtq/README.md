# Google Doc Tab Queue (GDTQ)

A deterministic, typed, single-writer queue/workflow runtime over Google Docs tabs.

## Logical operations

- `pop(A)` selects the first (or highest-scoring) application under state tab A
  and re-parents it to the sibling `A-onFlight`. It returns the stable tab ID,
  tab URL, latest event, and attempt ID.
- `push(A, B, item_id, event)` verifies membership in `A-onFlight`, appends a
  structured AJSS_EVENT JSON envelope to the application tab, and re-parents it
  directly under state B in one revision-checked Google Docs batchUpdate.
- `claim_item(A, item_id)` claims a specific tab for email, calendar, or human
  events rather than incorrectly popping a different application.
- `application_events(tab_id)` returns structured events, legacy journal entries,
  notes, and nested research-tab content. Research subtabs stay attached when the
  application tab changes state.
- `CompletionPublisher.complete` writes an immutable-id-named final JSON under
  the existing `Applications/<company-role>/` folder or under `Failed/<company-role>/`,
  verifies it via Drive read-back, then deletes the source document tab.

**Only the orchestrator invokes GDTQ**; processing agents return typed outcomes.
The schema registry lives under `schemas/gdtq/`. The complete, audited
state map and exhaustive per-agent routing are in `config/gdtq-routing.json`.
The event envelope includes previous and current state, agent, outcome and version.

## Set up

1. Configure the existing `config/runtime.json` using the repository's onboarding.
2. Configure Google OAuth (existing `application_service.google_auth`).
3. Put a searchable-text resume PDF in Drive and set `AJSS_RESUME_DRIVE_PATH`.
4. Configure `OPENAI_API_KEY` and optionally `OPENAI_MODEL` for preparation.
5. Run `python -m gdtq.cli doctor` to check configuration and schema coverage.
6. Run `python -m gdtq.cli run --database var/ajss-gdtq.sqlite3` under a single
   exclusive deployment; supervise with systemd or Docker.

`run` uses ATS API/scraping readers already in `adapters.job_boards`, deterministic
skill scoring and the existing application cover-letter agent. **No automatic browser
submission or outgoing email sending is granted by default.** To enable them,
inject consented browser/API submission strategies and authorized Gmail adapters into
`build_handlers`, `EmailCorrespondenceObserver`, and `Scheduler`.

## Recovery / delivery

- SQLite owns posting deduplication and durable scheduler cursors. It is not a
  ChatGPT-managed key/value service; persist/backup the DB volume.
- `recover_orphaned_inflight` requires exclusive instance ownership.
- A submission attempt is checkpointed *before* its external side effect. Any
  unconfirmed result is routed for manual reconciliation, not retried blindly.
- AI-generated board-specific code is saved as a **proposal**, never imported
  automatically. Require security review, sandbox tests and promotion.
- Google Docs is not a transactional message broker. The Docs tab movement and
  event append form one batch; the external SQLite claim and Drive archive require
  recovery/reconciliation. Use one writer per document.
- 100 tabs is the actual Docs limit. Configure a 90-tab soft cap, a 20-item
  manual queue cap, plus age-based expiry. Existing research subtabs count too.
- Run tests: `python -m pytest -q tests/test_gdtq.py`.

## Integration points still requiring deployment-specific configuration

The queue runtime is executable with the adapters configured as above.
Automated forms, Gmail sending, Calendar polling, research web tools and strong-model
fallback must be injected using real credentials/permissions. Without them, only
configured operations run; the code does not claim integrations succeeded.

The old `services.orchestrator` remains as a compatibility entry point. **Do not run it and GDTQ as
simultaneous writers to the same Google document.** Switch over under an exclusive
lock and migrate existing in-flight records first.

## Enable hourly Gmail/Calendar observation and daily reports

Set `AJSS_GDTQ_ENABLE_OBSERVERS=1`, `AJSS_REPORT_RECIPIENT`,
`AJSS_CONTACT_PHONE` and the existing Calendly URL configuration, plus
Google Gmail/Calendar OAuth permissions. The observers are then invoked hourly
by the same process, and the daily PDF is prepared at 08:00 Asia/Jerusalem.
The default `AJSS_GMAIL_ALLOW_SEND=0` creates drafts rather than sending.
Set to `1` only after explicitly authorizing account access and recipients.
This is a process-level opt-in; enabling it does not install Chrome or grant
browser submit permissions.

## Cleaned-up repository guidance

The root [README](../README.md), [architecture](../docs/architecture.md), [state map](../docs/state-machines.md), [agents](../agents/README.md) and [tests](../tests/README.md) now describe GDTQ as the primary design. Legacy Application Service provisioning, stage-task runners and PostgreSQL migrations remain only for compatibility. Obsolete diagrams, scratch diagnostics and IDE metadata are removed; do not reintroduce a competing queue/state workflow.
