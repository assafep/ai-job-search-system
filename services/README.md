# Services — reusable primitives and legacy runners

**The GDTQ scheduler/orchestrator is in `gdtq/`, not `services.orchestrator`.** Do not run the old and new writers concurrently against the same Google document.

## Reused services

| File | Role in GDTQ |
| --- | --- |
| `agent_runner.py`, `test_runner.py` | Structured prompt + JSON validation for compatible legacy agent prompts |
| `document_generator.py` | Combined application-package PDF generation |
| `runtime_config.py` | Existing onboarding/runtime settings, Google document/folder URLs |
| `external_event_polling.py` | Company-to-application matching helper used by observer integration |
| `pipeline_agents.py`, `bookmark_read.py` | Compatibility flows and reference posting-retrieval behavior |

## Legacy stage-task services

The many `services.stage_*`, `services.workload_*`, `services.orchestrator`, `services.polling`, approval and report components belong to the **previous Application Service stage runtime**. They are retained to avoid breaking old API/onboarding/tests, but are not invoked by the default GDTQ CLI. Stage YAML under `stages/` is also legacy. Do not use it as a second state-transition configuration.

GDTQ's authority is `config/gdtq-routing.json` with worker-specific schema enums; see [GDTQ](../gdtq/README.md), [main README](../README.md) and [architecture](../docs/architecture.md).
