# GDTQ runtime and operational contract

For graphical system views, see the [maintained Mermaid architecture diagrams](../docs/architecture.md): components, GDTQ sequence, state lifecycle, tab hierarchy, and runtime deployment.

GDTQ is the **sole AJSS workload runtime**. Processing agents are stateless producers of JSON Schema-conformant outcomes; the deterministic WorkloadOrchestrator coordinates operations.

## Queue contract

For application tab `itemId`, `pop(A)` moves it from root state tab `A` into sibling `A-onFlight`. A successful processing outcome resolves state B; `push(A, B, itemId, event)` appends a structured event to the application tab and moves it **directly from A-onFlight to B**. Both operations retain the same Google Docs tab ID and URL.

`claim_item(A,itemId)` claims one specific item for a Calendar, Gmail or human update. `application_events(itemId)` combines structured processing envelopes, legacy journal text/notes, and nested interview research tabs.

Agent outputs are validated in `gdtq.policy.OutcomeValidator` against `schemas/gdtq/`; `StateTransitionEngine` maps every enumerated outcome via `config/gdtq-routing.json` and checks against the legal state graph. The orchestrator handles dispatch, completion, quarantine, retention, and redrive.

## Stages and adapters

- Discovered: check SQLite identity (canonical URL, source ID, company-role); read posting using ATS APIs/scraping; optionally call an explicitly configured smart reader if deterministic readers fail. Save posting JSON under `Applications/<Company - Role>/`. Classify permanent vs retryable retrieval failures.
- Bookmarked: deterministic skill occurrence scoring relative to applicant profile; below-threshold applications are finalized as Invalidated.
- Validated: write tailored cover letter with the LLM prompt and a combined submission PDF using `gdtq.document_generator`.
- Prepared: invoke only explicitly approved external submission strategies, with a durable receipt before clicking Submit. Timeout or ambiguous result must not cause a repeated submission; route to manual reconciliation.
- Applied/Interviewing: optional hourly Gmail and Calendar observers; the latter invokes the research prompt and creates a nested application tab. Calendar and email idempotency keys are stored in SQLite.
- Human updates: `gdtq.manual.manual_transition`; DLQ redrive requires explicit approver and reason.
- Completion: write and verify `Applications/<Company - Role>/final-event-<tab-id>.json` (or `Failed/...`), then delete the live application tab.

The `gdtq.recipes` registry only stores **proposed** board-specific source code. It is never dynamically imported or deployed without review and tests.

## Deployment

Install Python 3.12+ requirements; configure `config/runtime.json` with Google Docs/Drive URLs, set Google OAuth refresh credentials, `OPENAI_API_KEY`, and `AJSS_RESUME_DRIVE_PATH`. The target Drive root must contain `Applicant/applicant_profile.json` and the resume PDF.

```bash
python -m pip install -r requirements.txt pytest
python -m gdtq.cli doctor
python -m pytest -q tests/
python -m gdtq.cli run --database var/ajss-gdtq.sqlite3
```

The default process operates a one-second round-robin tick, bounded worker concurrency, and no browser submission capability. Configure authorized site strategies explicitly before enabling real submissions.

Optionally set `AJSS_GDTQ_ENABLE_OBSERVERS=1` with Gmail/Calendar OAuth scopes, `AJSS_REPORT_RECIPIENT`, `AJSS_CONTACT_PHONE` and a Calendly URL. The daily report is scheduled for 08:00 Asia/Jerusalem. Gmail remains **draft-only** unless `AJSS_GMAIL_ALLOW_SEND=1` and authorized scopes are supplied.

Run one process **per journal**. Google Docs allows 100 tabs in a document, counting nested research tabs. A configured soft cap of 90 and manual retention/expiry policy should prevent capacity exhaustion.

## Operational safety

Google Docs event insertion and reparenting are in one revision-checked batch. SQLite leases, Google Docs updates and Drive archives are **not** an atomic multi-service transaction. An interrupted claim requires reconciliation under exclusive ownership. `recover_orphaned_inflight(..., exclusive_instance=True)` is an operator-only tool; never run concurrently with active workers. Back up SQLite and application artifacts.

No old Application Service, REST/MCP API, PostgreSQL migrations, or legacy stage runners are present. Non-production live Google integration tests are required before treating this architecture as production-ready.
