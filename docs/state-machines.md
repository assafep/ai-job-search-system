# State machines

## Application lifecycle

Source of truth: [`ALLOWED_TRANSITIONS`](../application_service/state_machine.py).
The chart includes every allowed change between distinct lifecycle states.
Labels describe API permissions; stage definitions and automation policy decide
which transition to request. All writes pass through the Application Service.

```mermaid
stateDiagram-v2
    state "Discovered" as S0
    state "Bookmarked" as S1
    state "Validated" as S2
    state "Invalidated" as S3
    state "Prepared" as S4
    state "Manual Apply Required" as S5
    state "Applied" as S6
    state "Interviewing" as S7
    state "Dead Letters Queue" as S8
    state "Rejected" as S9
    state "Offer" as S10
    state "Duplicate" as S11
    S0 --> S1
    S0 --> S8
    S0 --> S11
    S0 --> S5
    S1 --> S8
    S1 --> S11
    S1 --> S3
    S1 --> S5
    S1 --> S4
    S1 --> S2
    S2 --> S8
    S2 --> S11
    S2 --> S5
    S2 --> S4
    S3 --> S1
    S4 --> S6
    S4 --> S8
    S4 --> S11
    S4 --> S7
    S4 --> S5
    S4 --> S10
    S4 --> S9
    S5 --> S6
    S5 --> S8
    S5 --> S11
    S5 --> S7
    S5 --> S10
    S5 --> S4
    S5 --> S9
    S6 --> S8
    S6 --> S7
    S6 --> S10
    S6 --> S9
    S7 --> S8
    S7 --> S10
    S7 --> S9
    S8 --> S6
    S8 --> S1
    S8 --> S0
    S8 --> S11
    S8 --> S7
    S8 --> S5
    S8 --> S4
    S8 --> S2
```

Same-state requests are safe no-ops in `validate_transition`, including terminal
states. Rejected, Offer and Duplicate have no outgoing lifecycle transitions.
Invalidated can return to Bookmarked. Dead Letters Queue is restored only by an
operator; permitted restoration targets appear above.

Prepared membership authorizes automatic submission. Failed auto-apply attempts
route to Manual Apply Required with an explanation; unconfirmed submissions must
be reconciled manually before retrying. System errors before submission route to
Dead Letters Queue. These policy rules narrow the API transition graph.

Journal completion/archive and duplicate removal are storage operations, not extra
lifecycle states. Completed application snapshots remain separate from active queues.

## Durable stage requests

Source: [`application_service/stage_tasks.py`](../application_service/stage_tasks.py).
These are execution requests from MCP/REST, not application statuses. A request
can select a named stage and snapshot its eligible application IDs. Existing
stage API names remain unchanged.

```mermaid
stateDiagram-v2
    [*] --> queued: validated request and eligible-ID snapshot
    queued --> running: exclusive worker claim
    running --> running: heartbeat
    running --> succeeded: execution finished
    running --> failed: execution failed
    running --> reconciliation_required: outcome uncertain
    running --> failed: operator reconciliation after stopping worker
    reconciliation_required --> failed: operator reconciliation after stopping worker
    succeeded --> [*]
    failed --> [*]
```

A running or reconciliation-required request blocks another claim. Claims are not
reissued on timeout. Finished requests cannot be retried or changed; repeating the
same status is idempotent. Reusing an admission key returns the original request;
changing its payload conflicts.

## Task execution registry (WorkloadRegistry)

Source: [`services/workload_registry.py`](../services/workload_registry.py).
Each workload registry record tracks a task for a named stage, distinct from a
whole execution request and from an application lifecycle state. A `WorkloadRegistry` entry has a `workload_id` and a `stage` identifying its
processing definition. The durable API command has a separate `stage_task_id`.

```mermaid
stateDiagram-v2
    [*] --> queued: enqueue
    queued --> running: claim below attempt limit
    failed --> running: retry below attempt limit
    awaiting_input --> running: resume below attempt limit
    queued --> dead_letter: claim at attempt limit
    failed --> dead_letter: claim at attempt limit
    awaiting_input --> dead_letter: claim at attempt limit
    running --> succeeded: complete
    running --> failed: failure below attempt limit
    running --> dead_letter: failure or recovery at attempt limit
    running --> queued: provider deferral or incomplete recovery below limit
    running --> awaiting_input: wait for input
    dead_letter --> queued: explicit reopen resets attempts
    succeeded --> [*]
```

Provider deferral and waiting for input refund the current attempt. Recovery and
reopening are registry capabilities, not authorization to retry an uncertain
submission or consume the application Dead Letters Queue automatically. Application
policy and submission reconciliation checks still apply.

## Native ChatGPT task lifecycle

Source: [`chatgpt_tasks.py`](../application_service/chatgpt_tasks.py).
These are tasks within one admitted stage request, not application states.

```mermaid
stateDiagram-v2
    [*] --> pending: eligible ID and versioned context
    pending --> paused: CAPTCHA / sign-in / upload / native browser unavailable
    paused --> completed: validation result or definitive pre-submission failure
    pending --> completed: validated website observation and match decision
    pending --> submission_started: durable intent before final submit click
    paused --> submission_started: first submission intent after resolving interaction
    submission_started --> paused: specific host interaction required
    submission_started --> completed: observed confirmation or unconfirmed result
    paused --> completed: record result for an already-begun submission
    completed --> [*]
```

A begin-command replay returns `may_submit: false`. A paused begun attempt cannot
start again. Uncertain persistence or changed execution contracts move the whole
run to `reconciliation_required`. The service does not automatically reclaim
these tasks or restart website actions on a timeout.
