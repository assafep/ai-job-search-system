# GDTQ worker deployment

The repository only deploys **one GDTQ worker**, not the former REST/MCP Application Service.

## Requirements

- Python 3.12+ or Docker Compose
- A configured Google document with source/target queue tabs and a Drive root
- Google OAuth refresh-token environment variables or an equivalent authorized token
- A profile and resume PDF accessible in Drive, `AJSS_RESUME_DRIVE_PATH`
- `OPENAI_API_KEY` for cover letter and optional interview research
- Durable SQLite storage under `./var/` and a **single** active writer per document

## Local Docker

```bash
# keep secrets in a local .env file, never in Git
docker compose build gdtq-worker
docker compose up gdtq-worker
```

Mount a local `config/runtime.json` with `google_drive.interview_journal_url` and `google_drive.artifact_root_url`. The Compose service mounts `./var` for durable SQLite.

GDTQ runs a one-second round-robin scheduler. Real ATS API/browser submissions and outbound Gmail sending require separately provided, explicitly authorized adapters. Email/Calendar observers are opt-in and drafts are the default. Before a production cutover, test Google tab reparenting and lease recovery on a disposable document; back up both Docs artifacts and SQLite.
