# Processing agent contracts

GDTQ agents are domain-specific workers with **structured, enumerable outcomes**. They do not directly mutate the queue, submit applications, or send email. The deterministic `OutcomeValidator`, `StateTransitionEngine` and `WorkloadOrchestrator` own validity, routing and completion.

Contracts:
- Common and agent-specific JSON Schemas: [`schemas/gdtq/`](../schemas/gdtq/).
- Exhaustive outcome-to-state mapping: [`config/gdtq-routing.json`](../config/gdtq-routing.json).
- Execution status is separate from business result: `SUCCESS`, `RETRYABLE_FAILURE`, `PERMANENT_FAILURE`.
- Each worker may receive only its own schema and bounded application context.
- `bookmark_discovered` fetches posting details through existing ATS adapters; `validate_bookmarked` computes a deterministic unnormalized skill score; `prepare_validated` drafts a tailored cover letter/package; `submit_prepared` uses explicitly authorized submission strategies.
- Email, Calendar and Research agents have separate validation and side-effect boundaries.

The legacy `agents/*/prompt.md` and `output.schema.json` files are **reused as compatibility/LLM contracts** by `services.agent_runner` (not the routing authority). New stage outcome contracts live in `schemas/gdtq`.

See [architecture](../docs/architecture.md) and [GDTQ](../gdtq/README.md).
