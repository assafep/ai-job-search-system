# Job-search persistence API

The HTTP contract is in [`../openapi.yaml`](../openapi.yaml). With no runtime
configuration, the backend is an in-memory reference implementation. Once
onboarding creates `config/runtime.json`, the API automatically selects the
Google Docs-backed event store unless `REPOSITORY_BACKEND=memory` is explicitly
set.

Run locally with Python 3.7+:

```bash
python3 -m persistence_api.main
```

For long-running deployments, provide `GOOGLE_REFRESH_TOKEN`,
`GOOGLE_CLIENT_ID`, and `GOOGLE_CLIENT_SECRET` instead; the repository refreshes
access tokens as needed.

To use the existing Interviews Journal as the backend, set:

```bash
export REPOSITORY_BACKEND=google
export GOOGLE_ACCESS_TOKEN='...'
export GOOGLE_INTERVIEW_JOURNAL_URL='https://docs.google.com/document/d/...'
export GOOGLE_ARTIFACT_ROOT_URL='https://drive.google.com/drive/folders/...'
python3 -m persistence_api.main
```

The repository discovers the six top-level state tabs, resolves application tabs
under them, parses existing dated/bulleted journal events, and appends new
events in the same human-readable format. Nested research tabs remain attached
to their application. Artifact files are stored beneath the configured
single `Company / Role` folder and referenced from the application journal. By
default that folder is directly below the configured Drive root. Set the
optional `google_drive.application_root_path` runtime value (or
`GOOGLE_APPLICATION_ROOT_PATH`) to place all application folders below a
root-relative directory such as `applications/`; the profile is independent of
that setting and lives in `.ai-job-search-system` under the Drive root.

Applicant profile operations use `GET /profile` and `PUT /profile`; the Google
backend maps them to `.ai-job-search-system/applicant_profile.json` and migrates
previously misnamed files in place during profile writes. Workflow runtimes
read the canonical `application_defaults.calendly_url` and fall back to the
legacy `personal.calendly_url` field when present.

Internal workflow state belongs under
`.ai-job-search-system/workloads-internal-state/<workflow>/`. The `submit`
workflow uses `submission-receipts/` for current receipts and `receipt-history/`
for older duplicate files. `POST /storage/migrate-internal` moves existing
root-level profile and receipt files in place; repeating it is safe.

Use `GET /journal/status` to inspect the required top-level state tabs without
mutating the journal. If the response contains missing tabs, `POST
/journal/repair` explicitly creates only those missing top-level tabs through
the persistence API. Repair is never performed implicitly by reads or
onboarding. Duplicate top-level tabs are reported but never deleted or merged;
an applied repair exits unsuccessfully while duplicates remain.

Use `POST /applications/{application_id}/artifacts` to store generated PDFs or
other files. The request carries `name`, optional `mime_type`, and
`content_base64`; the backend stores the bytes and records the artifact in the
application journal. Retryable uploads should include an `idempotency_key`.
Use the matching `GET` endpoints to list artifacts or retrieve their content.

For workflow files that are not tied to an application, use `POST /files` with
a root-relative `path` such as `shared/research/company-notes.md`. Use
`GET /files?path=...` to read it back. Paths cannot be absolute or escape the
configured Drive root; nested directories are created as needed by the Google
backend.

Use `POST /applications/{application_id}/research` for interview-preparation
content. The Google backend creates a child tab below the application tab and
writes the supplied research text there.
Use `GET /applications/{application_id}/research` to read those child tabs
through the same persistence boundary.

Set `PERSISTENCE_API_TOKEN` to require `Authorization: Bearer <token>` on all
state endpoints; `/health` and `/ready` remain public for probes. `/health` is
the process liveness check, while `/ready` is `503` until the configured
persistence backend is available. Leave the token unset for local development.
