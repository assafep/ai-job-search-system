# Services

Services own deterministic behavior and side-effect boundaries. The existing
`persistence_api` package is the data-layer API. Onboarding is deliberately
small: it records Drive identifiers/URLs locally and never stores credentials.

`event_dispatcher.py` is the pure routing boundary: normalized adapter events
become queued workload envelopes, which are then handled independently by the
workflow runtime. It can optionally enqueue the envelope into
`WorkloadRegistry`; without a registry it remains a pure function.
For a scheduler or fixture-driven test, route one event with:

```bash
python3 -m services.event_dispatcher \
  --event-file tests/fixtures/events/calendar.json --enqueue
```

The `--enqueue` form uses `WORKLOAD_REGISTRY_PATH` and does not invoke an
agent or an external adapter.

`document_generator.py` returns a combined cover-letter/CV PDF as bytes. It
does not write to Drive; the workload sends the bytes through the persistence
API artifact endpoint.

`agent_runner.py` is the provider-neutral LLM boundary. It loads an agent's
prompt and output schema, invokes an injected provider, and validates the
structured result. The provider receives only prompt text and serialized
context; it does not receive Drive, mail, calendar, browser, or persistence
clients.

`approval_registry.py` persists human approval requests separately from
application state. It supports idempotent request creation, approved/rejected
decisions, and a pending-request report; it never performs the approved
external action.
When a workload payload contains an `approval_actions` array, the workflow
runtime evaluates those actions and records `ask` decisions in this registry
when one is configured. `allow` and `deny` remain outcomes for the eventual
external adapter; the runtime itself never performs that action.
Operators can inspect and decide requests with
`python3 -m services.approval_cli list` and
`python3 -m services.approval_cli decide <request_id> approved|rejected`.

`workload_registry.py` owns scheduler bookkeeping and recovery. It deduplicates
dispatched events, bounds retries, and requeues interrupted workloads; it does
not replace the persistence API as the owner of application state. Pass its
optional `storage_path` argument to persist registry records as JSON across
worker restarts; omit it for a process-local registry. Deployments can use
`registry_from_environment()` with `WORKLOAD_REGISTRY_PATH` and
`WORKLOAD_MAX_ATTEMPTS`.

`workflow_runtime.py` claims one queued workload, converts its normalized event
into workflow context, invokes the stateless workload executor, and settles the
registry record as succeeded or failed. It never receives direct Drive, Gmail,
Calendar, or browser access. `runtime_from_environment()` wires it to the
configured persistence API and workload registry for a deployment entrypoint
and requires completed onboarding by default. Unit tests may pass
`require_configuration=False`.
Call `WorkflowRuntime.run(..., agent_provider=provider)` when the runtime owns
agent invocation; the provider is called once per agent required by the
workflow. Supplying `agent_results` remains useful for replay and deterministic
test execution. Before provider invocation, the runtime reads profile,
application, event, and interview-research context through the persistence API
when those resources are available; only serialized values are passed to the
agent.

For `discover-and-apply`, the runtime invokes `job-discovery` first and passes
its normalized `candidates` to the application agent as
`discovered_candidates`. The application agent may return one package for a
single application or a `packages` array keyed by `posting_url`; the executor
resolves each package to the application created or deduplicated by the
persistence API before storing artifacts or appending events.

Reconciliation workloads first recover interrupted registry records, then may
record an application-scoped reconciliation result through the persistence API
when their context supplies `application_id` and `reconciliation_result`.
They also return the registry's counts and attention records for reporting.

`workload_worker.py` provides the continuous-drain seam. Construct it with a
`WorkflowRuntime` and an LLM provider, call `run_once()` from a scheduler, or
use `run_forever()` in a long-running worker. Provider and persistence failures
are returned as failed results while the registry retains retry state.

`worker_main.py` is the provider-neutral process entrypoint. Configure
`AGENT_PROVIDER=package.module:provider` only when overriding the default.
Without it, the worker uses the local Codex CLI provider, which requires
`codex --login` but no OpenAI API key. The imported value must be callable or
expose `generate(prompt, context)`. Run:

```bash
codex --login
python3 -m services.worker_main --once
python3 -m services.worker_main --once --limit 1
python3 -m services.worker_main --poll-seconds 30
```

The entrypoint loads runtime configuration, persistence API, workload registry,
and approval registry through the existing environment factories. It does not
give the provider access to external clients.

Journal state containers can be inspected without a write:

```bash
python3 -m services.journal_cli status
```

To explicitly create missing required top-level tabs, use:

```bash
python3 -m services.journal_cli repair --apply
```

The repair command is also available as `make journal-repair`; without
`--apply` it prints the missing tabs and proposed operations only. No repair is
performed unless `--apply` is present. Missing tabs may be added, but duplicate
tabs are never removed automatically and leave the applied command unsuccessful.

The runtime also has a deployable one-workload entrypoint:

```bash
python3 -m services.workflow_runtime \
  --workload-id work_... \
  --agent-results /path/to/agent-results.json
```

It requires onboarding, claims the workload from the configured registry, and
settles its status after execution.
