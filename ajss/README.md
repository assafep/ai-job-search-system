# AJSS runtime

The project is AJSS. This package is `ajss`.

| Package | Who uses it | When |
| --- | --- | --- |
| `ajss.onboard` | The operator, through `make onboard` | Before the worker starts |
| `ajss.worker` | `python -m ajss.cli run` | While applications are processed |
| `ajss.queue` | The worker | When a tab is claimed or moved |
| `ajss.documents` | Onboarding and the worker | When the profile or a cover letter is written |
| `ajss.submission` | The worker | When a prepared application is submitted |
| `ajss.cli` | The operator | doctor, onboard, and run |

GDTQ is the tab-queue type in `ajss.queue`, not the product name. `config/gdtq-routing.json` and `sql/gdtq/` keep that queue contract so the existing Neon index stays valid. Outcome schemas are in `schemas/`. Processing agents are stateless producers of JSON Schema-conformant outcomes; the deterministic WorkloadOrchestrator coordinates operations.

For graphical system views, see the [maintained Mermaid architecture diagrams](../docs/architecture.md).

## Queue contract

For application tab `itemId`, `pop(A)` moves it from root state tab `A` into sibling `A-onFlight`. A successful processing outcome resolves state B; `push(A, B, itemId, event)` appends a structured event to the application tab and moves it **directly from A-onFlight to B**. Both operations retain the same Google Docs tab ID and URL.

`claim_item(A,itemId)` claims one specific item for a Calendar, Gmail or human update. `application_events(itemId)` combines structured processing envelopes, legacy journal text/notes, and nested interview research tabs.

Agent outputs are validated in `ajss.queue.policy.OutcomeValidator` against `schemas/`; `StateTransitionEngine` maps every enumerated outcome via `config/gdtq-routing.json` and checks against the legal state graph. The orchestrator handles dispatch, completion, quarantine, retention, and redrive.

## Stages and adapters

- Discovered: check Neon PostgreSQL identity (canonical URL, source ID, company-role); read posting using ATS APIs/scraping; optionally call an explicitly configured smart reader if deterministic readers fail. Save posting JSON under `Applications/<Company - Role>/`. Classify permanent vs retryable retrieval failures.
- Bookmarked: deterministic skill occurrence scoring relative to applicant profile; below-threshold applications are finalized as Invalidated.
- Validated: write tailored cover letter with the LLM prompt and a combined submission PDF using `ajss.documents.generator`.
- Prepared: invoke only explicitly approved external submission strategies, with a durable receipt before clicking Submit. Timeout or ambiguous result must not cause a repeated submission; route to manual reconciliation.
- Applied/Interviewing: optional hourly Gmail and Calendar observers; the latter invokes the research prompt and creates a nested application tab. Calendar and email idempotency keys are stored in Neon PostgreSQL.
- Human updates: `ajss.worker.manual.manual_transition`; DLQ redrive requires explicit approver and reason.
- Completion: write and verify `Applications/<Company - Role>/final-event-<tab-id>.json` (or `Failed/...`), then delete the live application tab.

## Deployment

Install Python 3.12+ requirements; configure `config/runtime.json` with Google Docs/Drive URLs, set Google OAuth refresh credentials, `AJSS_LLM_PROVIDER`, `AJSS_LLM_API_KEY`, and `GOOGLE_RESUME_DRIVE_PATH`. The target Drive root must contain the résumé PDF. Onboarding writes `Applicant/applicant_profile.json` from that PDF.

```bash
python -m pip install -r requirements.txt pytest
python -m ajss.cli doctor
python -m ajss.cli bootstrap
python -m pytest -q tests/pipeline
python -m ajss.cli run --database var/ajss-gdtq.sqlite3
```

`bootstrap` prepares `var/` for the deterministic pipeline tests. A coding-agent login and a Google account can be absent; the command only reports whether they are already visible and does not connect them. The pipeline tests mock each board page and the form endpoint.

`python -m ajss.cli onboard` is the idempotent local-run checklist used by Codex and Grok. It does not start the worker. `--stage` runs one step on the same warm process. Read-only Gmail and Calendar keep their own refresh tokens. See [local run](../docs/local-run.md).

The default process operates a one-second round-robin tick, bounded worker concurrency, and no browser submission capability. Configure authorized site strategies explicitly before enabling real submissions.

Optionally set `AJSS_GDTQ_ENABLE_OBSERVERS=1` with Gmail/Calendar OAuth scopes, `AJSS_REPORT_RECIPIENT`, and a Calendly URL. Interview replies use the phone number on the applicant profile. The daily report is scheduled for 08:00 Asia/Jerusalem. Gmail remains **draft-only** unless `AJSS_GMAIL_ALLOW_SEND=1` and authorized scopes are supplied.

Run one process **per journal**. Google Docs allows 100 tabs in a document, counting nested research tabs. A configured soft cap of 90 and manual retention/expiry policy should prevent capacity exhaustion.

## Operational safety

Google Docs event insertion and reparenting are in one revision-checked batch. Neon PostgreSQL leases, Google Docs updates and Drive archives are **not** an atomic multi-service transaction. An interrupted claim requires reconciliation under exclusive ownership. `recover_orphaned_inflight(..., exclusive_instance=True)` is an operator-only tool; never run concurrently with active workers. Back up Neon PostgreSQL and application artifacts.

No old Application Service, REST/MCP API, PostgreSQL migrations, or legacy stage runners are present. Non-production live Google integration tests are required before treating this architecture as production-ready.

## Neon historical deduplication

Use [sql/gdtq/](../sql/gdtq/README.md) to backfill the 160 historical applications without dropping existing tables. A new database receives the empty queue tables from the onboarding wizard, including an empty `posting_keys` table when that legacy table is absent, instead of that backfill. The runtime selects `PostgresWorkIndex` from `DATABASE_URL` or `AJSS_DATABASE_URL` and only permits a local SQLite fallback with `AJSS_ALLOW_LOCAL_SQLITE=1`. The migration was successfully applied to Neon main on October 27, 2026 and validated (160 historical applications, 369 keys, zero missing/legacy-key loss).

Submission components, callers, activation and recovery provenance: [SUBMISSION.md](SUBMISSION.md).
