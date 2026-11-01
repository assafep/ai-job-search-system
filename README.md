# AJSS — AI Job Search System

AJSS is an AI-assisted job-search workflow whose state lives in Google Docs tabs and whose transitions are committed by one deterministic orchestrator.

## Architecture

**[Architecture diagrams — components, queue sequence, states, storage and deployment](docs/architecture.md)** are maintained in Mermaid and rendered on GitHub.

Discovery sources feed posting URLs into the `Discovered` tab. This first part of the lifecycle covers intake, bookmarking, and validation.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "background": "#000000",
    "primaryColor": "#000000",
    "primaryTextColor": "#ffffff",
    "primaryBorderColor": "#ffffff",
    "lineColor": "#ffffff",
    "textColor": "#ffffff",
    "edgeLabelBackground": "#000000"
  }
}}%%
flowchart TD
    S[Job posting URL discovered]
    D[Discovered<br/><small>source intake</small>]
    B[Bookmarked<br/><small>posting captured</small>]
    J(( ))
    V[Validated<br/><small>good fit</small>]
    P[Prepared<br/><small>application package</small>]
    SJ(( ))
    A[Applied<br/><small>submission confirmed</small>]
    M[Manual Apply Required<br/><small>automatic submission unavailable</small>]
    I[Interviewing<br/><small>follow-up and research</small>]
    R[Rejected<br/><small>terminal</small>]
    O[Offer<br/><small>terminal</small>]
    E[Expired<br/><small>retention timeout</small>]
    G[Ghosted<br/><small>interview timeout</small>]
    INV[Invalidated<br/><small>below threshold · terminal</small>]

    S --> D
    D -->|bookmark_discovered| B
    B -->|validate_bookmarked| J
    J -->|match| V
    J -->|below threshold| INV
    V -->|prepare_validated| P
    P -->|submit_prepared| SJ
    SJ -->|confirmed submission| A
    SJ -->|unconfirmed / unavailable| M
    A -->|email / calendar confirmation| I
    A -->|rejected| R
    A -->|retention timeout| E
    I -->|offer| O
    I -->|rejected| R
    I -->|interview timeout| G
    M -->|user moves tab manually| A
    M -->|email_correspondence: receipt confirmed| A

    classDef stage fill:#000000,stroke:#ffffff,color:#ffffff
    classDef start fill:#000000,stroke:#ffffff,color:#ffffff
    class S,D,B,J,V,P,SJ,A,M,I,R,O,E,G,INV stage
```

`Auto` means system-driven; `User` means manual action; `Auto / User` means either can cause the transition.

| State | What happens | Transition actor |
| --- | --- | --- |
| `Discovered` | AJSS records the posting as a new candidate item. | Auto |
| `Bookmarked` | `bookmark_discovered` fetches and records the posting details. | Auto |
| `Validated` | `validate_bookmarked` confirms that the posting matches the candidate profile. | Auto |
| `Invalidated` | The posting is below the matching threshold and becomes terminal. | Auto |
| `Prepared` | `prepare_validated` creates the tailored application package and submission materials; automatic submission may lead to `Applied` or `Manual Apply Required`. | Auto |
| `Manual Apply Required` | Automatic submission was unavailable or unconfirmed; the user can move the tab to `Applied`, or `email_correspondence` can advance it after detecting a confirmed receipt. | Auto |
| `Applied` | `submit_prepared` records a confirmed application submission. | Auto / User |
| `Expired` | The application remains inactive past the retention timeout. | Auto |
| `Interviewing` | Email and calendar signals confirm an interview or follow-up activity. | Auto / User |
| `Offer` | The candidate receives a confirmed offer. | Auto / User |
| `Rejected` | The application reaches a confirmed rejection outcome. | Auto / User |
| `Ghosted` | Interview-stage activity remains unanswered past the retention timeout. | Auto |

### AJSS at a glance

| Area | Responsibility |
| --- | --- |
| Google Docs tabs | The live application queues and workflow state |
| GDTQ | The tab-queue abstraction: `pop(A)` → `A-onFlight` → `push(A,B,…)` |
| Orchestrator | Validates typed outcomes, routes transitions, and commits state |
| Google Drive | Applicant materials, resumes, application packages, and completion artifacts |
| Neon PostgreSQL | Identities, leases, idempotency, and scheduler checkpoints |

GDTQ keeps each application's tab URL stable while moving it between parent state tabs. Agent outcomes are defined by JSON Schemas in `schemas/gdtq/` and routed by `config/gdtq-routing.json`.

The Google Docs client, Drive artifact store and OAuth helpers live under `adapters/google/`; ATS/API/scraping adapters under `adapters/job_boards/`; the OpenAI provider under `adapters/llm/`. AJSS's GDTQ runner and PDF generator are self-contained.

## Local setup

Requires Docker Compose. The live worker additionally needs a Google Docs journal and Drive root, an applicant profile at `Applicant/applicant_profile.json`, a résumé PDF accessible in Drive, and OAuth credentials.

Build the development image and run the deterministic checks inside it:

```bash
make setup
make verify
```

Individual checks are also available:

```bash
make doctor
make test
```

Run `make setup` again whenever dependencies or the Dockerfile change. No host Python installation or virtual environment is required. `make run` starts the worker through Docker Compose.

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

For containers, see [deployment](deployment/README.md). For **on-demand ChatGPT/MCP access on Vercel**, use [Vercel gateway instructions](deployment/VERCEL.md). The authenticated service exposes read-only history/dedup tools; dispatch is separately disabled until live integration is approved. Full queue contract, recovery policy and limitations: [GDTQ](gdtq/README.md). State map: [config/gdtq-routing.json](config/gdtq-routing.json).

## Repository

| Location | Purpose |
| --- | --- |
| `gdtq/` | AJSS workload runtime and GDTQ queue abstraction: orchestration, stages, completion, observers, SQLite and CLI |
| `adapters/google/` | Google OAuth, Docs tab operations, Drive artifact store and journal readers |
| `adapters/job_boards/` | ATS APIs, parsing, scraping and submission response contracts |
| `adapters/google_events.py` | Read-only Gmail and Calendar event sources |
| `adapters/llm/openai_provider.py` | Stateless model provider |
| `agents/application/`, `agents/interview-prep/` | Prompt and schema assets for AI stages |
| `config/gdtq-routing.json`, `schemas/gdtq/` | State graph and exhaustive typed business outcomes |
| `tests/` | Deterministic unit and adapter-boundary tests |
| `deployment/` | AJSS Docker images and operator guide |

## Safety and correctness

Submission attempts are checkpointed before external actions; uncertain results require reconciliation instead of another click. Completed applications are archived in Drive and read back before deletion from Docs. Generated browser recipes are stored for review, not automatically imported. Google Docs writes and SQLite commits do not form a distributed transaction: use one writer, an operator recovery policy, and backups for the SQLite identity index.

## Existing Neon history

The existing `ajss-db` contains 160 applications and 240 historical posting keys; 46 applications were missing any key. See [the idempotent Neon migration](sql/gdtq/README.md), which preserves the legacy tables and backfills the new GDTQ index. Set `DATABASE_URL` to activate the PostgreSQL implementation. **Migration applied and verified in Neon main on October 27, 2026**: 160/160 applications covered, 369 keys, all 240 original keys preserved. The production worker is not yet deployed.
