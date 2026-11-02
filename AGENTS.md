# AJSS contributor instructions

The project and the Python package are both **AJSS**. `ajss.onboard` is the operator checklist, `ajss.worker` is the running process, and `ajss.queue` is the Google Docs tab queue. The class `GDTQ` is that queue, not the product name. There is no legacy Application Service or stage-task API. Do not add new dependencies on `application_service`, `services`, `provisioning`, `stages`, `sql` or `api`.

- Queue semantics: `pop(A)` re-parents the item under `A-onFlight`; `push(A,B,item,event)` appends the event and moves the item to B.
- Agents only produce typed outcomes. `ajss.queue.policy.OutcomeValidator` and `StateTransitionEngine` validate/route; `WorkloadOrchestrator` alone commits state and controls terminal completion.
- Never auto-retry an uncertain application submission. Never fabricate posting content or successful submission receipts.
- Never load or execute AI-generated scraping/submitter code without code review, sandbox testing and explicit approval.
- One active writer per Google document; persist and back up SQLite indexes.
- Shared I/O lives in `adapters/google`, `adapters/job_boards`, `adapters/google_events`, and `adapters/llm`.
- No secrets or candidate personally identifiable information in logs, Git history, or test fixtures.

Run `python -m ajss.cli doctor` and `python -m pytest -q tests/` after every change. Live Gmail/browser/Google integration requires explicit permission and end-to-end validation in a non-production environment.

When the user asks Codex or Grok to run AJSS locally, follow [agents/local-run/AGENTS.md](agents/local-run/AGENTS.md). Lead with `make onboard`, one incomplete step at a time, and the links that command prints. Do not start `python -m ajss.cli run` until that checklist reports ready and the user agrees. Do not put secrets in the chat, the shell history, or Git.
