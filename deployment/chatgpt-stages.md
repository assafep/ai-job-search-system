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
