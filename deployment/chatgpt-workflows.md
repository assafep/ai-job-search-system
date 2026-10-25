# Workflow execution from ChatGPT

`validate-bookmarked` and `submit-prepared` execute website interactions in
**ChatGPT's native Browser/Computer Use**. The MCP server never launches a local
browser, hosted browser, CLI agent or API model for these tasks. Use a ChatGPT
host where both the AJSS MCP connection and native browser are available.
The Codex CLI/IDE session alone does not supply ChatGPT's native browser.

## Configure the connection

Deploy the repository using `./deployment/ctl.sh connect`, preserving its existing
OAuth configuration. Keep these Application Service settings:

```text
AJSS_WORKFLOW_RUNS_ENABLED=1
AJSS_MCP_TOOL_PROFILE=workflows
AJSS_WORKLOAD_FILE_BACKEND=database
```

The deployment publishes these from `config/runtime.json`:

```json
{"chat_workflows":{"enabled":true,"mcp_tool_profile":"workflows","internal_file_backend":"database"}}
```

Refresh the ChatGPT app's tool catalog after deployment. Select the AJSS app and
use ChatGPT's native browser in the same conversation. A suitable request is:

> Use AJSS to validate all Bookmarked positions. Open each posting with your
> native browser, extract the complete role description, evaluate it against my
> applicant profile, and record the result through the workflow tools.

For submission:

> Use AJSS to submit all Prepared applications in your native browser. Use each
> saved PDF and my applicant profile. Record the attempt before the final submit
> click and record the observed confirmation afterward. Never resubmit an
> uncertain attempt.

## Native-browser tool sequence

1. `listWorkflows` reports the active `execution_host`. Call
   `getWorkflowExecutionReadiness` for deployment prerequisites.
2. `startWorkflow` admits the scope and returns a durable run ID. Example:

```json
{"body":{"workflow":"validate-bookmarked","idempotency_key":"validate-request-001"}}
```

Omitting a limit snapshots **all** eligible IDs. `application_id` scopes one
posting; `limit` is only for an explicitly requested count. Do not combine them.
Reuse the admission key after a lost response; a deliberately new run uses a new
key. Items arriving after admission belong to a later request.

3. Call `nextChatGPTWorkflowTask` with the run ID and `body: {}`. It returns one
   task, an opaque task token, the posting URL, applicant data, and the deployed
   agent contracts. Repeated calls return the pending task. When `complete` is
   true, the admitted scope has finished or requires reconciliation.
4. For validation, ChatGPT opens the exact posting URL, extracts the complete
   role description, and makes the strong-match decision. Call
   `completeChatGPTValidation` with the task ID/token, a unique command key,
   `posting` matching the read-posting schema, and `decision` matching the
   validation schema. Concrete evidence is required for a strong match. The
   service reuses its posting parser and validation/completion planner, verifies
   the snapshot is current, and persists the result. Browser observations are
   supplied by ChatGPT; the server does not independently visit the website.
5. For submission, `getChatGPTSubmissionPackage` returns the exact validated PDF
   and SHA-256. ChatGPT fills the form using native host capabilities. Immediately
   before final submit, call `beginChatGPTSubmission` with the task ID/token and
   a unique command key. Only the first response's `may_submit: true` authorizes
   one final click. A replay returns `may_submit: false`.
6. Read the actual confirmation in the browser and call
   `completeChatGPTSubmission` with a different command key and an `outcome`.
   Success requires confirmation text and the observed page URL. An uncertain
   result must be `Submission unconfirmed`; failed attempts follow the existing
   Manual Apply Required / pre-submission system-error policy.
7. Call `nextChatGPTWorkflowTask` again until finished, then retrieve the readable
   PDF using `getWorkflowRunReport`.

Each effect records its intent before writing. Repeating the same command key
and payload returns the saved outcome without repeating application writes.
Changing the payload under that key conflicts. The task is bound to its admitted
application, profile/events snapshot, prepared-package hash and deployed agent /
execution-code contract. A stale context or uncertain effect cannot silently
start another attempt.

## Browser interruptions

Use `pauseChatGPTWorkflowTask` for a specific CAPTCHA, sign-in, file-upload step,
or unavailable native browser. Ask the user for that interaction and preserve the
task. Pausing is not an additional Prepared approval gate. Never substitute a
backend browser. After a begun submission is interrupted, inspect for confirmation
without clicking submit again; otherwise record an unconfirmed outcome.

Browser support depends on the ChatGPT host. Some sites remain blocked, and the
built-in browser can require manual file uploads. See the [official browser
documentation](https://learn.chatgpt.com/docs/browser). This integration does not
promise CAPTCHA bypass or unattended completion on every site.

## Vercel execution for the other nine workflows

The managed subscriber is `application_service.vercel_subscriber`, declared in
`deployment/vercel/pyproject.toml`. Vercel Queues delivers independent work units;
PostgreSQL stores run progress, workload/approval registries and mutation intents.
No daemon, local checkout or local registry is needed for this path. Redelivery
never re-executes an uncertain unit. The two ChatGPT workflows are rejected by
this subscriber and by the workstation claim endpoint.

Bookmarking only moves discovered links into Bookmarked; it does not open their
websites. Website extraction happens in ChatGPT validation. Preparation consumes
the resulting saved posting artifact and the Drive-backed original résumé.

Cloud activation is explicit:

```json
{"workflow_execution":{"vercel_enabled":true}}
```

Before enabling this in runtime.json and redeploying, configure an API model
credential (`OPENAI_API_KEY` or `XAI_API_KEY`) for hosted agent workflows, complete
applicant onboarding so the résumé is portable, and verify Drive artifact creation. New My Drive files use the existing owner
`drive.file` grant, then move into the selected folder for service-account access;
IDs are durably allocated before creation.
These credentials and a remote browser are **not** needed for the two native
ChatGPT browser workflows. An unavailable hosted configuration remains queued
without attempting a posting. Stop the legacy workstation worker when activating
the cloud split; do not run another registry-writing scheduler alongside it.

The supplied Cron job runs daily at 05:00 UTC and admits only the nine Vercel
workflows. It never starts a ChatGPT browser session. It uses a generated
`CRON_SECRET`; the endpoint rejects unauthenticated calls and does nothing while
cloud execution is disabled. The interview default remains daily. Vercel Hobby
supports daily Cron; preserving minute-level automatic polling requires a plan
that supports it and a corresponding Cron configuration. On-demand requests use
Queues and do not wait for the next daily Cron.

## Monitoring

For monitoring, call `listWorkflowRuns` with `{}` to discover durable requests.
Optional arguments are `status` (`queued`, `running`, `succeeded`, `failed`, or
`reconciliation_required`), `workflow` (exact name), `limit` (1–200; default 50),
and `cursor`. Results contain `runs` and `next_cursor`; pass that cursor with
the same filters to fetch the next page, until it is null. Runs are ordered by
creation time descending, then run ID descending. Concurrent status updates can
change filtered membership between pages. Each record includes public status,
timestamps, resolved scope, and any summary/report metadata; claim tokens and
worker IDs are excluded. Listing never claims, retries, or updates work.

The equivalent authenticated REST endpoint is
`GET /workflow-runs?status=running&limit=50`; Python callers can use
`ApplicationServiceClient.list_workflow_runs(status='running')`. This lists
API-admitted requests, including ChatGPT requests, rather than legacy local
`config/workloads.json` entries. Inspect `updated_at` on running requests for
stale heartbeats; listing does not prove that a worker process is alive.
Refresh the ChatGPT app's tool catalog after deploying to discover the new tool.


Public status omits task capabilities, stored applicant context and internal
queue work-unit records. Run progress includes completed/admitted counts.

## Reconciliation

Running or reconciliation-required requests block another installation-wide run.
No lease timeout reclaims a ChatGPT submission. Stop the original browser/worker,
inspect receipts and application events, and reconcile any uncertain submission.
An explicit operator can then use authenticated REST:

```text
POST /workflow-runs/{run_id}/reconcile
{"worker_stopped":true,"note":"Verified outcomes and stopped the original executor"}
```

This closes the old run as failed; it never retries it. The internal reconcile,
worker-claim and worker-update operations are not exposed as MCP tools.

## Legacy workstation compatibility

`services.chat_workflow_worker` is an explicitly opt-in compatibility runner
for the other workflows. It requires `AJSS_LEGACY_WORKSTATION_WORKFLOWS=1` on
the service; it is disabled by default. Disabled cloud execution leaves requests
queued instead of falling back to this worker. It cannot claim or execute the two
native ChatGPT browser workflows. Its systemd service is named
`ajss-chat-workflow-worker`. It is not the executor for ChatGPT browser tasks.
Workflow-owned checkpoints, receipts and PDFs continue to use the Application
Service `/files` boundary; database-backed internal files retain Drive fallback
reads. Journal/profile/application artifacts stay in their configured storage.
