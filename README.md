# AJSS — AI Job Search System

AJSS is a Google Docs–backed application workflow. **GDTQ (Google Doc Tab Queue) is the intended runtime.** The former Application Service, stage-task API, provisioning workflows and legacy queue runners remain in this repository for migration and selected reusable capabilities; they are **not** the new execution architecture.

> **Release status:** GDTQ has deterministic unit tests and an injectable runtime, but Google Docs live reparenting, outbound email, and browser submission are not end-to-end certified. Do not run GDTQ and `services.orchestrator` against the same document.

## Architecture

```text
Google Docs (one tab per application)
  Discovered ─pop─> Discovered-onFlight ─push─> Bookmarked
  Bookmarked ─pop─> Bookmarked-onFlight ─push─> Validated / Invalidated
  Validated  ─pop─> Validated-onFlight  ─push─> Prepared
  Prepared   ─pop─> Prepared-onFlight   ─push─> Applied / Manual Apply Required
                               |
                               v
     WorkloadOrchestrator -> OutcomeValidator -> StateTransitionEngine
                               |
                    terminal outcome / expiry
                               v
           Google Drive Applications/<Company - Role>/
                final-event-<application-tab-id>.json
```

- **Queue operations:** `gdtq.queue.GDTQ` claims and moves direct child tabs; a claim moves A → `A-onFlight`, and a push moves `A-onFlight` → B. The application's tab ID and URL are preserved.
- **State rules:** `config/gdtq-routing.json`; per-agent output contracts: `schemas/gdtq/`. Processing agents produce outcomes; the orchestrator alone commits transitions.
- **Persistence:** the Google document is the human-readable active journal; Google Drive stores posting content, preparation packages, research and final archives. SQLite (`gdtq.store.WorkIndex`) indexes posting identities, operation leases and scheduler checkpoints. Keep its volume persistent.
- **Workers:** `gdtq.dispatch`, `gdtq.runtime`, `gdtq.handlers`; fair round-robin queue selection, bounded asynchronous workers and retry backoff. Email and Calendar observers are optional and disabled by default.
- **Terminal states:** Offer, Rejected, Duplicate, Invalidated, Failed, Expired and Ghosted are archived before the live tab is deleted. Failed posting downloads go to the Drive `Failed/` tree; unconfirmed submissions require manual reconciliation.
- **Extension boundary:** job-board ATS adapters, Google OAuth, PDF preparation, LLM providers and selected helpers are reused from `adapters/`, `services/` and `application_service/`. Proposed AI-generated scraping/submission recipes are **quarantined for review**, not automatically installed.

See [GDTQ component, operational and migration instructions](gdtq/README.md), [architecture](docs/architecture.md) and [state map](docs/state-machines.md).

## Setup and quick verification

Use Python 3.12+ and run the existing onboarding only when the Google Drive folders, candidate profile or OAuth grant are missing. **Do not start the legacy stage runner afterward.**

```bash
python -m pip install -e . pytest
python -m gdtq.cli doctor
python -m pytest -q tests/test_gdtq.py
```

For a **single-writer, explicitly authorized** GDTQ process, configure `config/runtime.json` through onboarding, Google credentials, an accessible resume PDF, and the model provider:

```bash
export AJSS_RESUME_DRIVE_PATH='Applicant/candidate-resume.pdf'
export OPENAI_API_KEY='(supply using your secret manager)'
python -m gdtq.cli run --database var/ajss-gdtq.sqlite3
```

The process scheduler ticks once per second and checks queue states in round-robin order. Live submission automation is not enabled by the default CLI: an authorized browser/API submission strategy must be injected and tested. An absent strategy leads to manual follow-up, not a fabricated success.

Optional correspondence/Calendar/research/report integrations are gated by `AJSS_GDTQ_ENABLE_OBSERVERS=1`; recipient and Google OAuth scopes are required. The Gmail adapter defaults to draft-only. See [GDTQ README](gdtq/README.md) for exact settings. Never commit credentials or `config/runtime.json`.

## Repository map

| Location | Status | Purpose |
| --- | --- | --- |
| `gdtq/` | **Current** | Queue, orchestration, validation, scoring, stages, completion, observers, CLI |
| `config/gdtq-*`, `schemas/gdtq/` | **Current** | Versioned lifecycle, typed outcomes, runtime defaults |
| `tests/test_gdtq.py` | **Current** | Deterministic contract and adapter-boundary tests |
| `adapters/job_boards/`, `adapters/google_events.py` | Shared | ATS readers and read-only Gmail/Calendar clients |
| `services/agent_runner.py`, `services/document_generator.py` | Shared | Structured LLM calls and application PDF generation |
| `application_service/google_docs_repository.py`, `google_drive_artifacts.py` | Shared | Google Docs/Drive clients, history, artifacts and OAuth support |
| `services/orchestrator.py`, `stages/`, `api/`, `sql/` | **Legacy / compatibility** | Previous stage-task and persistence system; not GDTQ scheduler |
| `provisioning/`, `deployment/` | Mixed | Existing environment provisioning; GDTQ service deployment needs explicit wiring |

## Tests and deployment boundaries

The focused GDTQ suite uses fake Docs/Drive adapters and does not access Google accounts or submit applications. Run `python -m pytest -q tests/test_gdtq.py`; GitHub Actions uses `.github/workflows/gdtq-tests.yml`. Run the legacy suite separately before retiring the Application Service.

**Migration:** back up the journal, Drive artifacts and durable identity index; stop all legacy writers; provision GDTQ root/in-flight tabs; verify current and in-flight tab placement; run integration tests with non-production documents; then start one scheduler. No destructive live migration is automatically performed.

See [deployment](deployment/README.md), [test instructions](tests/README.md), [GDTQ](gdtq/README.md), [agent contracts](agents/README.md), and [adapters](adapters/README.md).
