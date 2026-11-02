# Tests

The current repository has one runtime: AJSS, using GDTQ for Google Docs tab queues.

```bash
python -m pip install -r requirements.txt pytest
python -m ajss.cli doctor
python -m ajss.cli bootstrap
python -m pytest -q tests/
```

`bootstrap` creates `var/` and a local SQLite index. It reports whether a coding-agent binary and Google credentials are already visible. It does not contact either account. `python -m pytest -q tests/pipeline` then runs the posting-to-submission tests. Those tests start from a posting URL, render Comeet, Greenhouse, Lever, and Ashby page scripts without executing JavaScript, and POST the application form to a mocked endpoint.

`make onboard` is the repeatable local-run checklist for a real résumé, journal, and database. It is documented in `docs/local-run.md` and `agents/local-run/AGENTS.md`. The checklist tests do not contact Google.

Tests use injected fake Google Docs/Drive/mail resources and temporary SQLite. No live OAuth, website submission or mailbox side effects occur. `.github/workflows/gdtq-tests.yml` runs the complete retained suite. Any new integration must have deterministic unit tests and a separate opt-in live smoke test. `tests/integration/` remains that opt-in live check.

## Isolated deterministic agent tests

Run only the agent unit suite:

```bash
docker compose --profile dev run --rm ajss-dev python -m pytest -q tests/agents/
```

Each test owns fresh mocks. `tests/agents/conftest.py` provides in-memory mocked
checkpoints, a fixed date/time, and synthetic posting/application data. It blocks
socket connections/DNS, subprocess creation, SQLite connections and psycopg
connections, and removes key runtime activation variables. Reads of repository
prompts/schemas are intentional. No candidate personal data appears in fixtures.
Pure helpers (schema validation, identity matching, serialization) remain real;
LLM providers, readers, stores, PDF generators, browser actions, mail, calendar,
research callbacks, reporting delivery and orchestrator commits are mocked.

| Agent or responsibility | Test file | Behavior checked |
| --- | --- | --- |
| application, interview-prep, read-posting, validate, applicant-profile, correspondence | `agents/test_prompt_contracts.py` | Exact prompt/context delivery, all provider interfaces, valid output, invalid JSON/schema, timeout, repeated output and application batch schema |
| bookmark_discovered | `agents/test_pipeline_agents.py` | Pre/post-fetch duplicate detection, archive calls, explicit fallback, permanent/transient failures |
| validate_bookmarked | `agents/test_pipeline_agents.py` | Exact scoring inputs and match/nonmatch mapping |
| prepare_validated | `agents/test_pipeline_agents.py` | Exact writer/PDF inputs, artifact bytes and keys, failures without writes |
| submit_prepared | `agents/test_pipeline_agents.py` | Attempt persisted before action, confirmed receipt replay, uncertain outcomes never retried, definite non-submission fallback |
| email_correspondence | `agents/test_observer_agents.py` | Classification, one report per message, unique active match, follow-up replies only, draft default, failure boundaries |
| calendar_observer | `agents/test_observer_agents.py` | One research/report per meeting, unmatched/inactive/ambiguous skips, research failure |
| research | `agents/test_observer_agents.py` | Supplied history, fixed date, saved briefing, invalid/timeout reports never saved |
| DailyReportAgent | `agents/test_observer_agents.py` | Exact summary/PDF/delivery arguments, rendering failure never delivers |
| manual_update, phone_followup | `agents/test_manual_and_inventory.py` | Exact outcome payloads for supported targets, forbidden transitions have no effect |

Inventory checks fail when a new prompt directory or configured workflow agent
has no declared test coverage. The existing full suite also tests real scoring,
policy routing, SQLite, fake document transitions and handler wiring; those are
separate from isolated agent tests. CI's existing `pytest -q tests/` command
includes the new suite automatically.

These tests verify deterministic contracts and decisions given controlled
inputs. Mock LLM responses cannot prove live model accuracy, prompt-injection
resistance or browser compatibility. Browser sandbox and authorized live
integration validation remain separate work.
