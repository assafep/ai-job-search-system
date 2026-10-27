# Run stage tasks from ChatGPT

The Application Service owns application-state reads and writes. MCP tools are
generated from `openapi.yaml`; do not maintain a second handwritten catalog.
The remote endpoint remains `https://<deployment>/mcp`, with the existing OAuth
or authenticated stdio transport. `AJSS_MCP_TOOL_PROFILE=stages` selects the
operational stage tools; `domain` includes state-management operations.

## Request and monitor work

Use `listStages` to inspect definitions and execution ownership. Call
`startStageTask` with a body containing `stage`, a non-empty `idempotency_key`,
and optionally `application_id` or `limit`. Omitted limits select every eligible
application in the Google journal. Retry with the same key. Monitor using
`listStageTasks`, `getStageTask`, and `getStageTaskReport`. A task has a
`stage_task_id`; a list response contains `tasks` and `next_cursor`.

Durable execution commands live in PostgreSQL under `stage_tasks`; in-progress
job applications still live in Google Docs. Completed application archives are
separate PostgreSQL tables. One execution command may cover several applications
or a maintenance operation with no application IDs.

## Native browser stages

`validate-bookmarked` and `submit-prepared` execute only through ChatGPT native
Browser/Computer Use. Use `nextChatGPTStageTask` to obtain the application,
agent contract and current task context. Validation results go through
`completeChatGPTValidation`. Submission uses `getChatGPTSubmissionPackage`,
`beginChatGPTSubmission` immediately before the final click, and
`completeChatGPTSubmission` after observing confirmation. The service verifies
scope, snapshots, capabilities and idempotency before persisting results.

A nested browser task is identified by `task_id`; its parent execution command
is identified by `stage_task_id`. Keep both identifiers when posting results.
Do not run these browser stages through a CLI, hosted browser, workstation
worker, model API, or adapter. Use `pauseChatGPTStageTask` for a specific CAPTCHA,
login, upload, or missing browser capability. Prepared membership authorizes
submission; interruption never authorizes submitting again without verification.

## Optional local Comeet submission

The default `submit-prepared` execution host remains ChatGPT native Browser/Computer
Use. Operators may explicitly enable the existing Playwright/Chrome Comeet
adapter **on the Application Service and its workstation worker**:

```bash
export AJSS_SUBMISSION_BROWSER_BACKEND=local-playwright
export AJSS_STAGE_TASKS_ENABLED=1
export AJSS_WORKSTATION_STAGES=1
```

Install the repository's Python dependencies, Node.js, the Playwright npm
package and a Chrome/Chromium executable on the workstation worker. The
browser adapter uses `BROWSER_EXECUTABLE` when set, otherwise searches the
usual Chrome executable names. Extra-question generation uses the configured
xAI provider credentials. This backend is **not** a feature of a serverless
Application Service deployment: the worker requires a persistent host with
the browser runtime.

Restart/redeploy **both** service and worker with identical settings before
starting a new stage task. `listStages` should now report
`submit-prepared.execution_host=workstation`. Use the normal
`startStageTask` API/MCP operation with `stage=submit-prepared` and an
idempotency key. The workstation worker claims the task, selects Prepared
Comeet applications, reads their existing PDFs, and dispatches exactly once
through `ComeetJobBoard.submit_application`. A command like
`python3 -m services.stage_task_worker --once` consumes one queued task on
an installation configured for workstation stages.

There is no manual `SubmissionContext` construction and no need to call
Playwright directly. The browser adapter uses a combined résumé/cover-letter
PDF as **one** résumé attachment. A separate cover-letter form attachment is
left blank rather than pretending the combined file is a separate letter.

Only Comeet applications are admitted into local submissions; other Prepared
applications remain eligible when the native backend is selected. Backend
ownership is stored on each durable task at admission. Changing the setting
does not migrate queued/running tasks; reconcile or complete them under their
original execution host before changing backend. Local validation of bookmarks
is **not** supported: `validate-bookmarked` stays ChatGPT native.

The same safety rules apply to both backends: a durable receipt precedes any
browser dispatch, confirmed receipts alone move applications to Applied,
blocked/unconfirmed results are recorded, and ambiguous outcomes are never
automatically retried. Human verification and unfamiliar form controls require
manual attention. Use a **single test application** before enabling queue-wide
runs, since Comeet form variants are not fully validated live.

To revert for **new** tasks, after resolving existing claims:

```bash
export AJSS_SUBMISSION_BROWSER_BACKEND=chatgpt-native
```

## Workstation execution

`python3 -m services.stage_task_worker` consumes non-browser commands through
`/stage-tasks/claim`, executes them through `StageExecutor`, and posts progress
and report references through the API. Enable it explicitly with
`AJSS_WORKSTATION_STAGES=1`; command admission requires
`AJSS_STAGE_TASKS_ENABLED=1`. Hosting the HTTP API on Vercel does not start this
worker. Use one registry-writing executor per installation.

Interrupted execution can require reconciliation even if no application was
submitted, for example if report publication did not finish. Check the task's
scope, browser tasks, intent markers and stored results before changing its
state. Reconciliation requires the worker to be stopped and a written note.

## Refresh the connected MCP

After deploying tool-definition changes, open the existing AI Job Search System
plugin's detail page at ChatGPT Plugins and refresh its connection. Keep the
same MCP URL and authentication. Verify the new tool catalog, permissions,
`listStageTasks`, and journal queue counts before executing work. Regenerate
local handoff artifacts with `./deployment/ctl.sh chatgpt` where applicable.
See [the coordinated cutover](../docs/stage-task-cutover.md).
