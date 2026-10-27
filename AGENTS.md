# AJSS contributor instructions

**Only GDTQ is the runtime.** There is no legacy Application Service or stage-task API. Do not add new dependencies on `application_service`, `services`, `provisioning`, `stages`, `sql` or `api`.

- Queue semantics: `pop(A)` re-parents the item under `A-onFlight`; `push(A,B,item,event)` appends the event and moves the item to B.
- Agents only produce typed outcomes. `gdtq.policy.OutcomeValidator` and `StateTransitionEngine` validate/route; `WorkloadOrchestrator` alone commits state and controls terminal completion.
- Never auto-retry an uncertain application submission. Never fabricate posting content or successful submission receipts.
- Never load or execute AI-generated scraping/submitter code without code review, sandbox testing and explicit approval.
- One active writer per Google document; persist and back up SQLite indexes.
- Shared I/O lives in `adapters/google`, `adapters/job_boards`, `adapters/google_events`, and `adapters/llm`.
- No secrets or candidate personally identifiable information in logs, Git history, or test fixtures.

Run `python -m gdtq.cli doctor` and `python -m pytest -q tests/` after every change. Live Gmail/browser/Google integration requires explicit permission and end-to-end validation in a non-production environment.
