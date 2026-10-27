# Google Docs and Drive storage

GDTQ uses **Google Docs tabs as live queue membership**, not Google Drive file-parent movement. Each state has a top-level tab and an `<State>-onFlight` sibling. The application is one child tab with a stable tab ID; nested research tabs follow the application through state moves.

`gdtq.queue.GDTQ` uses the existing revision-checked `application_service.google_docs_repository.GoogleDocsClient`; `gdtq.downstream.CompletionPublisher` uses `application_service.google_drive_artifacts.GoogleDriveArtifactStore`. In-flight `pop(A)` moves the item to `A-onFlight`, then `push(A,B,item,event)` appends a structured event and moves the same tab directly to B.

Documents have a **100-tab limit**, counting root, in-flight, application and research tabs. A 90-tab soft capacity policy expires eligible low-priority/manual items only after archiving their complete history. Completed JSON resides under `Applications/<Company - Role>/final-event-<tabId>.json` or `Failed/<Company - Role>/...`; posting and submission artifacts stay under the same application folder. Applicant profile: `Applicant/applicant_profile.json`.

Auth and identifiers are provisioned through the existing onboarding; never commit credentials. See [GDTQ runbook](../../gdtq/README.md) and [storage model](../../docs/storage-backends.md).
