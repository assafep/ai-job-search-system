# Stage/task contract cutover

This is a breaking development change. There are no parallel route names,
request-field aliases or import shims. A source rollout and data migration must
be coordinated; do not rename the live registry while old code is serving it.
The new store refuses an uninitialized registry instead of presenting an empty
queue. Migration 005 initializes an empty registry on fresh installations.

1. Stop every workstation consumer and native-browser executor. Disable command
   admission. Inspect interrupted tasks without retrying browser submissions.
2. Capture any execution settings from the existing installation. Deploy the new
   service with `AJSS_STAGE_TASKS_ENABLED=0` and `AJSS_WORKSTATION_STAGES=0`.
   Recreate execution settings through `/stages/execution`; the configured Drive
   settings filename is `.ai-job-search-system/stage_execution.json`.
3. Apply `sql/migrations/005_stage_task_names.sql` and insert its filename into
   `ajss_schema_migrations` in the same transaction. The existing provisioning
   migration runner records the ledger entry automatically.
4. Verify the operational resource is `stage_tasks`, request keys are `stage`,
   records use `stage_task_id`, and IDs/statuses/claims/scopes match the pre-cutover
   snapshot. Completed application and event counts must not change.
5. Convert any existing local operational JSON files while workers are stopped:
   `python3 -m provisioning.stage_task_cutover config/workloads.json config/approvals.json config/workload-effort.json`
   Supply only paths that exist. The converter validates conflicts, keeps private
   backups and atomically replaces files; repeated conversion is a no-op.
   Deploy the consuming worker, CLI and configuration from this same revision.
   Environment names, the `stages` effort map, Python imports, systemd unit and
   MCP profile must match the new source. Do not start any worker yet.
6. Refresh the AI Job Search System plugin connection from its detail page in
   ChatGPT Plugins, retaining the same `/mcp` endpoint and authentication. Review
   the discovered tools and their permissions. The new catalog includes
   `listStages`, `startStageTask`, `listStageTasks`, `getStageTask` and
   `getStageTaskReport`. Verify `listStageTasks` and journal queue counts before
   performing mutations. Regenerate local handoff files with
   `./deployment/ctl.sh chatgpt` when using that deployment flow.
7. Activate only the intended executor. Existing queued commands do not authorize
   draining queues as part of a terminology migration. Review stale commands and
   their captured execution hosts/scopes separately.

OpenAI's MCP quickstart instructs refreshing the plugin connection after server
tool or metadata changes:
https://developers.openai.com/plugins/build/app-quickstart

## What the deployed development database contains

The October 26 inspection found 160 completed application rows (138 Rejected,
22 Invalidated), plus a separate registry of 23 execution commands. Nine commands
were queued, and one submission command required reconciliation. That submission
command had zero application IDs, zero browser tasks and a `finishing=true`
marker; this is consistent with interrupted report finalization, not evidence of
an uncertain application submission. State must still be reviewed before clearing
it. The migration preserves command state and does not execute or cancel it.
