# External adapters

Adapters perform bounded operations on third-party systems. Processing agents receive serialized context and return typed outcomes; they do **not** get direct credentials or ownership of state changes.

## Shared adapters used by GDTQ

- `job_boards/` implements ATS API-first posting reads and controlled scraping. The new `gdtq.handlers` reuse `adapters.job_boards.posting_details`.
- `google_events.py` supplies read-only Gmail and Calendar event sources. GDTQ observers perform application matching, classification and idempotent checkpointing.
- `llm/` supplies provider implementations used by the existing `services.agent_runner` and new cover-letter/research calls.
- `browser/` contains older site-specific implementations and compatibility recipes. GDTQ does **not** automatically execute these; configure and authorize submission strategies explicitly.
- `google-drive/`, `gmail/`, `calendar/`, and `calendly/` document integrations built on existing Application Service/Google access tokens. `gdtq.gmail` holds new draft/send logic.

All external writes must be authorized and idempotent where possible. Never automatically execute AI-generated scraper/submission code; proposals remain in review storage until tested and approved.

See [GDTQ](../gdtq/README.md), [main architecture](../docs/architecture.md) and [job-board API](job-boards/README.md).
