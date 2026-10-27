# AI prompt contracts

The two AI prompt assets used by the current implementation are `application/` (cover letter) and `interview-prep/` (research briefing). They are invoked through `gdtq.agent_runner.run`, which enforces their JSON Schemas.

**Business outcomes** such as `BOOKMARKED`, `BELOW_THRESHOLD`, `SUBMITTED` or `MANUAL_REQUIRED` are not authored in these prompt schemas. Their schemas are in `schemas/gdtq/` and exhaustively mapped by `config/gdtq-routing.json`.

Processing agents cannot directly move queue items, send emails, or submit forms. Explicit approved adapter callbacks perform external I/O.
