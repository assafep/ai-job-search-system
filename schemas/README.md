# Schemas

**Current GDTQ contracts:** [`gdtq/`](gdtq/) contains the shared `processing-outcome.schema.json`, `event-envelope.schema.json` and one outcome schema per registered worker (bookmarking, validation, preparation, submission, email, calendar, research and manual transitions). Each outcome `code` enum must have an exhaustive mapping in [`config/gdtq-routing.json`](../config/gdtq-routing.json). `gdtq.policy.StateTransitionEngine` validates this at initialization.

**Shared applicant schema:** `applicant-profile.schema.json` continues to describe the persisted candidate profile, used by onboarding and several adapters.

The older `agents/*/output.schema.json` contracts remain **provider prompt-output schemas**; they do not replace GDTQ's outcome envelopes. Keep payload `execution_status` distinct from domain `code`. See [state map](../docs/state-machines.md).
