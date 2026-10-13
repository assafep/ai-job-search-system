# Job Search Automation

This repository contains four independent, event-driven workflows. Workflow
agents are stateless decision-makers: they read context, produce structured
decisions, and do not directly create side effects. Workload instances use the
data-layer API to read and write state.

The portable persistence-boundary instructions are in
`skills/persistence-api/SKILL.md`; `openapi.yaml` remains the authoritative
request/response contract.

Before running a workflow, ensure `config/runtime.json` exists. If it does not,
run the onboarding command described in `README.md`.

## Boundaries

- Agents may not call Google Drive, Gmail, Calendar, or browser APIs directly.
- Adapters expose external systems to workflow runtimes.
- The data-layer API is the only owner of application-state mutations.
- Every write must be explicit, validated, and idempotent where retries are possible.
- Ready for Auto-Apply queue membership authorizes automatic submission; do not
  pause that queue for human approval. Other actions follow
  `config/automation-policy.yaml`.

## Application and reporting policy

- Do not require human review before moving a successfully prepared, saved
  application package from `Bookmarked` to `Ready for Auto-Apply`.
- Every failed auto-apply attempt must move to `Manual Apply Required`; its
  transition event must include the auto-apply agent's failure explanation.
  Ready for Auto-Apply items must be submitted automatically. Unconfirmed
  submissions must be reconciled manually before anyone retries them.
- Failed `prepare-bookmarked` workloads must also move the posting to
  `Manual Apply Required`, with the preparation failure reason in the transition
  event. This includes retrieval, validation, generation, and storage failures.
- User-facing activity reports must be readable PDFs, not JSON. Machine-readable
  checkpoints may remain JSON under workload-owned internal state.
- Concurrent queue runs must share a synchronized persistence client and locked
  workload/approval registries. Use `services.concurrent_queues` for an eight-thread
  Bookmarked/Ready for Auto-Apply pass; never assign the same posting to two
  workers or run a separate registry-writing process alongside it.

## Testing

Use `python3 -m services.test_runner` to test one agent contract or one
workflow in dry-run mode. The test runner does not require `config/runtime.json`
and does not call external adapters.
