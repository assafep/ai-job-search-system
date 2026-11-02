# AI prompt contracts

`local-run/AGENTS.md` is the operator guide for the Codex and Grok CLIs. It is not a pipeline prompt and has no output schema. When the user asks either CLI to run AJSS locally, that file is the flow: `make onboard`, one missing value at a time, then an explicit start.

The callable AI prompt assets are `application/` (cover letter), `interview-prep/` (research briefing), `read-posting/`, `validate/`, `applicant-profile/`, and `correspondence/`. The latter four were recovered from commit `079823d^`. They are available through the runner with an explicitly supplied provider; restoring them does not change the deterministic default scoring or enable external actions. They are invoked through `ajss.documents.prompts.run`, which enforces their JSON Schemas.

**Business outcomes** such as `BOOKMARKED`, `BELOW_THRESHOLD`, `SUBMITTED` or `MANUAL_REQUIRED` are not authored in these prompt schemas. Their schemas are in `schemas/` and exhaustively mapped by `config/gdtq-routing.json`.

Processing agents cannot directly move queue items, send emails, or submit forms. Explicit approved adapter callbacks perform external I/O.
