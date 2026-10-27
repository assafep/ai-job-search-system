# Google platform adapters

This package is the standalone Google boundary. No REST/MCP Application Service is required.

- `auth.py`: `token_from_environment()` loads `GOOGLE_ACCESS_TOKEN`, the refresh-token triple `GOOGLE_REFRESH_TOKEN`/`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, or `GOOGLE_SERVICE_ACCOUNT_JSON`.
- `docs.py`: `GoogleDocsClient` uses the Docs API's `includeTabsContent` and revision-checked `batchUpdate`; helpers parse existing human-written journal notes.
- `drive.py`: `GoogleDriveArtifactStore` reads/writes root-relative paths, idempotency metadata and application folders.
- `journal.py`: pure journal parsing and old event reader compatibility within active Docs tabs.
- `paths.py`: root-relative Drive path validation.

The **Google Doc Tab Queue** is in `gdtq.queue`, not here. Queue movement is `A → A-onFlight → B`; the document and its nested tabs are the live application state. Only the orchestrator invokes queue mutations. Never expose access tokens or profile content in logs.

Gmail and Calendar reader implementations are in `adapters/google_events.py` and require appropriate OAuth scopes; Gmail sends are opt-in and draft-only by default.
