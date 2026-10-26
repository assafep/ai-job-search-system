# AJSS Application Service

The HTTP contract is in [`../openapi.yaml`](../openapi.yaml). With no runtime
configuration, the backend is an in-memory reference implementation. Once
onboarding creates `config/runtime.json`, the API automatically selects the
Google Docs-backed event store unless `REPOSITORY_BACKEND=memory` is explicitly
set.

Install the project dependencies from `requirements.txt` before starting the service
(the MCP dependencies are pinned in `requirements-mcp.txt`).

Run locally:

```bash
python3 -m application_service.main
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
python3 -m application_service.main
```

The repository discovers the six top-level state tabs, resolves application tabs
under them, parses existing dated/bulleted journal events, and appends new
events in the same human-readable format. Nested research tabs remain attached
to their application. Artifact files are stored beneath the configured
single `Company / Role` folder and referenced from the application journal. By
default that folder is `{Drive root}/Applications`. Set
`google_drive.application_root_path` or `GOOGLE_APPLICATION_ROOT_PATH` only to
choose a different root-relative parent. The profile is independent of that
setting and lives in `Applicant` under the Drive root.

Applicant profile operations use `GET /profile` and `PUT /profile`; the Google
backend maps them to `Applicant/applicant_profile.json` and migrates
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
the Application Service. Repair is never performed implicitly by reads or
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

Set `APPLICATION_SERVICE_TOKEN` to require `Authorization: Bearer <token>` on all
state endpoints; `/health` and `/ready` remain public for probes. `/health` is
the process liveness check, while `/ready` is `503` until the configured
persistence backend is available. Leave the token unset for local development.


## MCP endpoint

The same service exposes MCP at **`/mcp`**. Use the deployed service origin plus `/mcp` for the connection URL. This is a stateless Streamable HTTP transport using JSON responses,
implemented with the official MCP SDK. Authenticated requests use `POST`;
`GET` and `DELETE` return `405`. Use an MCP client to initialize, list tools,
and call tools rather than opening the URL as a page. Request bodies are limited
to 4 MiB. `/health` and `/ready` remain the HTTP health probes.

The tool catalog is generated from [`../openapi.yaml`](../openapi.yaml): tool
names are the REST operations' `operationId` values. Path and query parameters
are top-level tool arguments; a JSON request body is the `body` argument.
Start with `journalQueueCounts` and empty arguments, or `listApplications` with
`{"status": "Prepared"}`. Tools return the HTTP status and data in MCP
`structuredContent`, with failures marked `isError`.

Calls use the existing HTTP handlers, validation, authentication, and persistence
locking. Writes require an explicit requested operation, and tools whose body
supports `idempotency_key` require a non-empty key. Reuse that key on retries;
a transport failure can leave the outcome uncertain, so read state before retrying.
The default catalog includes state-changing and destructive domain operations.
For ChatGPT workflow control, set `AJSS_MCP_TOOL_PROFILE=workflows` to expose only
workflow execution and relevant state reads. The workflow tools enqueue durable
requests; execution remains in existing Python runtimes. Workflow agents receive
no direct access to these tools or external systems.

See [ChatGPT workflow execution](../deployment/chatgpt-workflows.md) for worker
setup, tool arguments, reporting, and interrupted-run reconciliation.

### Remote authentication

Remote clients can use either OAuth or the existing service bearer token:

- **OAuth:** use the `/mcp` URL with OAuth and dynamic client registration.
  The client discovers `/.well-known/oauth-protected-resource/mcp` and
  `/.well-known/oauth-authorization-server`, then uses `/register`, `/authorize`,
  `/token`, and `/revoke`. The owner enters the existing Application Service
  token on the AJSS consent page at `/mcp-oauth/consent`. That owner token is not
  returned to the client or callback. Authorization uses PKCE and the `ajss` scope,
  which grants read and write access to this installation's MCP tools.
- **Service token:** clients that support configured headers can send
  `Authorization: Bearer <service-token>` directly to `/mcp`.

OAuth access tokens last one hour. Refresh tokens last 30 days and rotate when
used. OAuth records persist in the database's `installation_resources` table;
configure database connectivity for OAuth. OAuth tokens are limited to this MCP
resource and do not authenticate direct REST requests. Rotating the service
owner token invalidates existing OAuth records, requiring clients to reconnect.

Set `AJSS_MCP_ISSUER` to the canonical public HTTPS **origin**, without `/mcp`.
A service token must be configured for MCP even when REST is intentionally
unauthenticated for local development. Accepted server variables are
`APPLICATION_SERVICE_TOKEN`, `PERSISTENCE_API_TOKEN`, or `AJSS_API_TOKEN`.
Unauthenticated MCP requests return `401` with an OAuth discovery challenge;
a cross-origin browser request is rejected with `403`.

### Local stdio connection

The same tool catalog is available through a local launcher:

```bash
./deployment/ctl.sh mcp-stdio
```

The launcher runs in the provisioning container and reads the saved deployed
HTTPS URL and bearer credential from the bootstrap checkpoint at startup.
It calls the REST API over HTTPS; it does not run the persistence backend locally.
For a direct Python launch, configure both `APPLICATION_SERVICE_URL` and
`APPLICATION_SERVICE_TOKEN` (or `AJSS_API_TOKEN`), then run
`python3 -m application_service.mcp_adapter`.

For connection generation, verification, and host registration, see the
[deployment guide](../deployment/README.md#mcp-connections) and
[provisioning handoff](../provisioning/README.md#chatgpt-and-codex-connection-handoff).

## Bulk item updates

`POST /bulk-updates` accepts `{"items":[...]}` with 1–25 operations. Each item
requires an `operation` and its own non-empty `idempotency_key`. Supported
operations are `update_application`, `transition_application`, `append_event`,
`complete_application`, `remove_duplicate`, `install_gmail_grant`, and
`acknowledge_gmail_notice`. The exact per-operation fields and response contract
are defined in `openapi.yaml`. For example:

```json
{"items":[
  {"operation":"update_application","application_id":"app-123",
   "idempotency_key":"correct-role-app-123-v1","expected_status":"Bookmarked",
   "data":{"role":"Senior Backend Engineer"}}
]}
```

The entire batch is validated before writes. Valid items execute independently
in input order; HTTP 200 includes ordered `results`, `succeeded`, and `failed`
counts. A mixed batch is not an all-or-nothing transaction across external
systems. Callers must inspect per-item statuses, including
`reconciliation_required`, rather than treating HTTP 200 as universal success.

Retry keys are scoped to an item, so retries can regroup batches without
reapplying successful updates. A changed payload under the same key is rejected.
The service saves a durable intent before mutation and a result afterward under
its workload-owned internal file storage. Interrupted mutations require manual
reconciliation; they are never blindly replayed. Receipts store fingerprints
and safe results, not Gmail credentials. Configure writable internal file
storage before using this endpoint.

Optional `expected_status` and `expected_updated_at` protect application updates
against stale selection. Hybrid installations hold the journal's shared lock
across each item's precondition check and domain mutation. Ghosting completion
also rechecks activity against `data.inactive_before`. Gmail notice
acknowledgements require the prepared grant's `refresh_expires_at`, so an old
notice cannot acknowledge a newly renewed grant. `remove_duplicate` refuses
other application states.

The Python client's `bulk_update_items(items, batch_size=25)` processes every
supplied item in bounded requests, preserving global result order. An empty list
makes no HTTP calls. Workflows 8 and 10 use batches of five completion/removal
items; workflow 9 uses bulk grant installation and acknowledgement. Notice
preparation and the adapter's message send keep their existing boundaries.

Workflow 8 reads `GET /journal/terminal-items` to select only terminal items in
the main journal, excluding archived applications. The existing single-item
and terminal-cleanup endpoints remain available. MCP exposes `bulkUpdateItems`
in the domain tool profile; the workflow-only profile continues to exclude
direct state mutation tools.

## Native ChatGPT browser execution

`validate-bookmarked` and `submit-prepared` use ChatGPT native Browser/Computer
Use. MCP provides task context, deployed contracts and saved packages; its result
tools validate and persist observations. The service never launches a replacement
browser or model for those tasks. `beginChatGPTSubmission` writes the submission
intent before the final click, and replay does not authorize another click.
See [the tool protocol and interruption rules](../deployment/chatgpt-workflows.md).

New Drive files can use the existing app-scoped owner login for creation and
placement, while the service account reads/updates them through the shared
directory. The hybrid backend durably allocates file IDs before creating them,
preventing duplicate files after a lost create response. This does not request
broader OAuth scopes. Existing files retain their IDs on replacement.
