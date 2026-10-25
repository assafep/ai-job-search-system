# ChatGPT workflow execution

ChatGPT calls the Application Service's existing authenticated `/mcp` endpoint.
The service queues durable requests in PostgreSQL `installation_resources`.
A separate, long-running Python worker executes the repository's existing
workflow runtime. HTTP requests never spawn background threads for execution.
The application database role needs the existing `002_runtime.sql` migration.

## Configure and start

Deploy this source revision to the Application Service and the workflow host.
Keep the existing OAuth configuration. Set these on the **Application Service**:

```text
AJSS_WORKFLOW_RUNS_ENABLED=1
AJSS_MCP_TOOL_PROFILE=workflows
```

The profile exposes workflow control and relevant read tools, excluding direct
transitions, deletion, repairs, registry claims, and worker completion tools.
The default `domain` profile preserves existing clients' domain operations.
To preserve these settings on future deployments, configure `chat_workflows`
in `config/runtime.json`:

```json
{"chat_workflows":{"enabled":true,"mcp_tool_profile":"workflows","internal_file_backend":"database"}}
```

Refresh the connected ChatGPT app's tool catalog after deploying.

On the **workflow host**, complete the normal onboarding/runtime configuration,
install the repository requirements and the configured workflow providers, and
configure `APPLICATION_SERVICE_URL` and `APPLICATION_SERVICE_TOKEN` for the same
installation. Start a supervised process:

```bash
python3 -m services.chat_workflow_worker
```

`--once` claims at most one request and then exits. The worker validates
`config/runtime.json` before claiming anything. Requests can stay queued while
the worker is offline; connecting MCP alone does not start the worker.

Use a single workflow host and a persistent checkout/registry volume. Stop other
orchestrators, schedulers, and manual registry-writing runners before starting
this worker. It holds `config/concurrent-queues.lock`, shared with the concurrent
queue CLI. Do not run a separate registry writer alongside it. Multiple chat
workers are also serialized by the service's durable installation-wide claim.

## Tools

1. Call `listWorkflows` with `{}` to discover deployed workflow names and scopes.
2. Call `startWorkflow` with a `body`, for example:

```json
{"body":{"workflow":"submit-prepared","idempotency_key":"submit-request-001"}}
```

This processes all currently eligible Prepared postings. For one posting, add
`"application_id":"app-123"`; for an explicitly requested count, add `"limit":5`.
Do not combine those fields. Queue IDs are snapshotted at admission and filtered
again against current eligibility at execution. Newly arriving postings belong
to a later request. There is no additional Prepared approval gate.

Scheduled workflows run their existing scheduled admission logic. External
workflows poll the appropriate adapter and process matching queued events;
ChatGPT does not supply invented email/calendar payloads. Scheduled and external
polls support whole-workflow scope only; scoped requests are rejected explicitly.
Existing failed scheduled workloads require reconciliation rather than an
implicit retry through this interface.

3. Poll `getWorkflowRun` with `{"run_id":"chat-..."}`.
4. Call `getWorkflowRunReport` with the same arguments for a readable PDF. Status
   includes the stored PDF metadata/path; the report tool returns PDF bytes as
   base64 with file metadata. Use the returned file URL when available, or render
   the returned PDF bytes as an attachment in the host. JSON checkpoints are not
   the user-facing report. A report not ready yet returns 409.

Reuse an idempotency key when a tool response is lost. Repeating a request returns
its original run and scope, including after completion; changing the request
under the same key is rejected. Starting another deliberate run needs a new key.
The service and worker compare blueprint digests before execution, and reports
record the worker code revision (`+dirty` denotes a modified checkout).

## Interruptions and reconciliation

`updated_at` is refreshed every 20 seconds during execution. A crashed worker
leaves the run `running`; a caught interruption becomes `reconciliation_required`.
Both block further claims across this installation. They are never reclaimed on
a timeout, because an application may have been submitted before connectivity
was lost. Check stale timestamps and the worker supervisor when investigating.

Stop the original worker, inspect application events and submission receipts,
and manually reconcile unconfirmed submissions before starting any new work.
Then explicitly release the block through authenticated REST (not exposed to
ChatGPT's tools):

```text
POST /workflow-runs/{run_id}/reconcile
{"worker_stopped":true,"note":"Describe verified outcomes and reconciliation"}
```

This keeps the old run failed and does not retry it. Restore dead letters only
through the existing explicit operator procedure. Resuming the worker may then
consume queued requests. If reporting/storage fails during execution, the run
also requires investigation; the worker attempts a failure PDF when storage is
available. A hard process crash cannot generate a final PDF.

Internal worker endpoints are `POST /workflow-runs/claim` and
`POST /workflow-runs/{run_id}/worker-update`. They require normal service bearer
authentication; completion additionally requires the opaque claim token. Tokens
are omitted from public run status. These endpoints are excluded from MCP.

## Supervised workstation worker

On a Linux workstation with the configured agent/browser tools and saved API
credentials, install the user service using the project's virtualenv:

```bash
.venv/bin/python -m provisioning.chat_worker
systemctl --user status ajss-chat-workflow-worker
```

This uses the existing workload and approval registries and private API
credential file. The workstation must remain online. Manage it with
`systemctl --user stop|start|restart ajss-chat-workflow-worker`; stop it before
running another registry-writing orchestrator on this host. This user service
is enabled for login sessions; it is not an always-on cloud worker.

## Workflow-owned file storage

For hybrid installations, `chat_workflows.internal_file_backend=database`
publishes `AJSS_WORKLOAD_FILE_BACKEND=database`. The existing `/files` API then
stores paths beneath `.ai-job-search-system/workloads-internal-state/` in the
service's existing PostgreSQL runtime table. Checkpoints, submission receipts,
and report PDFs remain behind the Application Service and its path validation.
Idempotent file writes commit their content and retry receipt together. Older
files remain readable from Drive when no database file exists. Journal/profile
and application artifacts retain their configured storage. The default `drive`
setting never silently switches storage after a failed write.
