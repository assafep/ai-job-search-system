# AJSS — Google Doc Tab Queue job automation

AJSS processes job applications with a **single GDTQ WorkloadOrchestrator**. There is no Application Service, REST/MCP server, PostgreSQL, legacy scheduler, or parallel stage-task runtime.

## Architecture

**[Architecture diagrams — components, queue sequence, states, storage and deployment](docs/architecture.md)** are maintained in Mermaid and rendered on GitHub.

```text
Google Docs: Discovered -> Bookmarked -> Validated -> Prepared -> Applied -> Interviewing
            pop(A) -> A-onFlight -> processing outcome -> push(A,B,item,event)
                                       |
                         OutcomeValidator + StateTransitionEngine
                                       |
                         WorkloadOrchestrator
                            | nonterminal: another Docs queue
                            | terminal: completion record in Drive
Google Drive: Applicant/profile + resume; Applications/company-role/job posting/package;
              Applications/company-role/final-event-<tab-id>.json
SQLite: canonical posting identity, processing leases, idempotency and scheduler checkpoints
```

GDTQ moves an application's existing Google Docs tab between parent state tabs; its tab URL stays stable. The **source queue** owns its on-flight sibling. Agent outcomes are enumerated by JSON Schemas in `schemas/gdtq/` and routed by `config/gdtq-routing.json`. All transitions and queue writes belong to the deterministic orchestrator.

The Google Docs client, Drive artifact store and OAuth helpers live under `adapters/google/`; ATS/API/scraping adapters under `adapters/job_boards/`; the OpenAI provider under `adapters/llm/`. The GDTQ agent runner and PDF generator are self-contained.

## Local setup

Requires Python 3.12+, a Google Docs journal and Drive root, an applicant profile at `Applicant/applicant_profile.json`, a résumé PDF accessible in Drive, and OAuth credentials.

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
python -m gdtq.cli doctor
python -m pytest -q tests/
```

Create local `config/runtime.json` (ignored by Git) with the Google Docs/Drive URLs:
```json
{
  "google_drive": {
    "interview_journal_url": "https://docs.google.com/document/d/YOUR_DOC_ID/edit",
    "artifact_root_url": "https://drive.google.com/drive/folders/YOUR_FOLDER_ID"
  },
  "scheduling": {"calendly_url": "https://calendly.com/your-account"}
}
```

Configure a Google access token or refresh-token credential set using environment variables documented in `adapters/google/README.md`. Set `AJSS_RESUME_DRIVE_PATH`, `OPENAI_API_KEY`, and run one process:
```bash
python -m gdtq.cli run --database var/ajss-gdtq.sqlite3
```

**Do not enable against a production journal before validating tab movement on a test document.** Live browser submissions require authorized injectable browser/API strategies; the CLI does not perform them by default. Optional mail/calendar/research/report integrations are behind `AJSS_GDTQ_ENABLE_OBSERVERS=1`, and Gmail send is disabled without `AJSS_GMAIL_ALLOW_SEND=1`.

For containers, see [deployment](deployment/README.md). Full queue contract, recovery policy and limitations: [GDTQ](gdtq/README.md). State map: [config/gdtq-routing.json](config/gdtq-routing.json).

## Repository

| Location | Purpose |
| --- | --- |
| `gdtq/` | WorkloadOrchestrator, GDTQ queue, stages, completion, observers, SQLite, CLI and tests |
| `adapters/google/` | Google OAuth, Docs tab operations, Drive artifact store and journal readers |
| `adapters/job_boards/` | ATS APIs, parsing, scraping and submission response contracts |
| `adapters/google_events.py` | Read-only Gmail and Calendar event sources |
| `adapters/llm/openai_provider.py` | Stateless model provider |
| `agents/application/`, `agents/interview-prep/` | Prompt and schema assets for AI stages |
| `config/gdtq-routing.json`, `schemas/gdtq/` | State graph and exhaustive typed business outcomes |
| `tests/` | Deterministic unit and adapter-boundary tests |
| `deployment/` | Dedicated GDTQ Docker image and operator guide |

## Safety and correctness

Submission attempts are checkpointed before external actions; uncertain results require reconciliation instead of another click. Completed applications are archived in Drive and read back before deletion from Docs. Generated browser recipes are stored for review, not automatically imported. Google Docs writes and SQLite commits do not form a distributed transaction: use one writer, an operator recovery policy, and backups for the SQLite identity index.
