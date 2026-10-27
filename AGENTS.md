# AJSS coding agent instructions

## Primary design

The supported target is **GDTQ**. Start with `README.md`, `gdtq/README.md`, `docs/architecture.md`, `config/gdtq-routing.json`, and `schemas/gdtq/`.

- Queue A owns A-onFlight. `pop(A)` moves A → A-onFlight; `push(A,B,item,event)` moves A-onFlight → B. Never insert a destination in-flight hop.
- Stateless processing agents return schema-valid **business outcomes** and separate `execution_status`; they do not change state or directly send email/submit.
- `OutcomeValidator` checks the agent contract. `StateTransitionEngine` exhaustively resolves outcomes using versioned policy. Only `WorkloadOrchestrator` authorizes a lifecycle transition.
- A failed or ambiguous outbound application submission **must not be re-submitted blindly**. Persist an unconfirmed receipt and require reconciliation.
- Invalid agent output is quarantined to DLQ with an error event; terminal outcomes are archived and verified before removing the live tab.
- Use the existing ATS reader, PDF builder, OAuth and Docs/Drive primitives rather than introducing competing implementations.
- SQLite is an authoritative **identity and dispatch checkpoint**, not the Google Docs application data store; keep its database on persistent disk and reconcile with document writes.
- Never execute AI-generated website code automatically; review, sandbox, test, then explicitly approve promotion.
- A single writer owns each Google document. Running the old `services.orchestrator` alongside GDTQ is forbidden.
- Do not expose resume contents, credentials, bearer tokens, recruiter messages, or applicant profile in logs.

## Commands

```bash
python -m gdtq.cli doctor
python -m pytest -q tests/test_gdtq.py
```

A live `python -m gdtq.cli run` requires onboarding, authorized Google scopes, a persistent SQLite volume and approved external actions. It is **not** a replacement for the legacy deployment bootstrap until end-to-end testing is complete.

## Legacy compatibility

`application_service/`, `services/`, `stages/`, `sql/`, `api/` and some deployment code still support the older stage-task API and tests. Do not casually delete helpers imported by `gdtq/` or public compatibility routes. Mark old components clearly and only remove them with a dependency audit and passing tests.
