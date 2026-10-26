# Job Search Automation

This repository contains time-driven polling stages. Stage
agents are stateless decision-makers: they read context, produce structured
decisions, and do not directly create side effects. Workload instances use the
Application Service to read and write state.

The portable persistence-boundary instructions are in
`skills/application-service/SKILL.md`; `openapi.yaml` remains the authoritative
request/response contract.

Before running a stage, ensure `config/runtime.json` exists. If it does not,
run the onboarding command described in `README.md`.

## Boundaries

- Stateless decision agents may not call Google Drive, Gmail, Calendar, or browser APIs directly.
- For `validate-bookmarked` and `submit-prepared`, the ChatGPT stage orchestrator
  uses ChatGPT native Browser/Computer Use. MCP supplies context and accepts structured
  observations/results; the Application Service validates and persists them. Never
  substitute a local CLI, Playwright, API model, or hosted browser for these website
  interactions. Pause for specific CAPTCHA, sign-in or upload interaction when the
  host requires it. Native-browser availability and CAPTCHA success are not guaranteed.
- Adapters expose external systems to stage runtimes.
- The Application Service is the only owner of application-state mutations.
- Every write must be explicit, validated, and idempotent where retries are possible.
- Prepared queue membership authorizes automatic submission; do not
  pause that queue for human approval. Other actions follow
  `config/automation-policy.yaml`.

## Application and reporting policy

- For bookmarking discovered positions, run `python3 -m services.run_single Discovered`
  (with the configured runtime environment). To read a single posting URL without
  any stage, use `python3 -m services.agent_cli read-posting --url https://...`.
- When the user requests a particular workload or queue without specifying a
  count, process all eligible positions in that requested scope. "Only
  auto-apply" limits the stage, not the number of positions. Do not interpret
  a command's default limit as a user-requested count. If the user names a
  specific position, keep execution scoped to that position; honor any explicit
  count or instruction to stop or not run.
- Do not require human review before moving a successfully prepared, saved
  application package from `Bookmarked` to `Prepared`.
- Every failed auto-apply attempt must move to `Manual Apply Required`; its
  transition event must include the auto-apply agent's failure explanation.
  Prepared items must be submitted automatically. Unconfirmed
  submissions must be reconciled manually before anyone retries them.
- System errors (including configuration, unexpected exceptions, invalid agent
  output, and preparation retrieval/validation/generation/storage failures) move
  the posting to `Dead Letters Queue`, with the reason in the transition event.
  This overrides manual routing for system errors before submission. Unconfirmed
  submissions still require manual reconciliation. Dead letters are never
  consumed automatically; an operator must restore the appropriate source state.
- User-facing activity reports must be readable PDFs, not JSON. Machine-readable
  checkpoints may remain JSON under workload-owned internal state.
- Concurrent queue runs must share a synchronized persistence client and locked
  workload/approval registries. Native ChatGPT tasks use durable per-item claims for Bookmarked/Prepared;
  never assign the same posting to two executors. `services.concurrent_queues`
  remains an isolated legacy/test utility, not the live entry point for these
  native-browser stages. Never run a separate registry writer alongside
  an installation’s active stage executor.

## Testing

Use `python3 -m services.test_runner` to test one agent contract or one
stage in dry-run mode. The test runner does not require `config/runtime.json`
and does not call external adapters.
