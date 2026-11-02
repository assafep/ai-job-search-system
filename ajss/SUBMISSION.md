# Submission ownership and usage

The GDTQ CLI supports an explicitly enabled Comeet backend. Default is disabled.
The stateless HTTP dispatch path keeps browser submission disabled.

| Component | Caller | Purpose |
|---|---|---|
| `ajss/submission/wiring.py:configured_strategies` | `ajss/cli.py:main` | Select disabled or Comeet backend |
| `ajss/submission/wiring.py:strategy` | `configured_strategies`, tests | Load prepared PDF and profile; call board framework |
| `ajss/submission/processing.py:submit_prepared` | `ajss/worker/handlers.py:submit` | Persist attempt before external action; validate and retain receipt |
| `adapters/job_boards/framework.py:submit` | Submission strategy | Select board adapter and validate result |
| `adapters/job_boards/submission/comeet/adapter.py:submit` | Framework's host callback | Validate destination; temporary PDF; invoke browser worker |
| `adapters/job_boards/submission/comeet/browser.cjs` | Python adapter subprocess | Verify employer board link, fill form, upload PDF, click once, observe receipt |
| `adapters/job_boards/submission/contracts.py` | Stage, framework, Comeet wrapper | Validate submission evidence |

The browser worker was recovered from `079823d^`, with the hardcoded Senior
filter removed and click uncertainty handling corrected. The unrelated Chrome
session script and dynamic Python module loader were not restored. The old
hardcoded LLM answer generator was omitted: missing required answers require
manual handling. The combined prepared PDF is used in both attachment slots.

Enable only after nonproduction browser validation and explicit live permission:
`AJSS_SUBMISSION_BACKEND=comeet`. The workstation must have Node, Playwright
and Chrome available; optionally set `BROWSER_EXECUTABLE`. The current Python
worker image does not supply these browser dependencies. No auto-install occurs.

An interrupted click or missing receipt is unconfirmed and must be reconciled.
Never clear its durable checkpoint merely to retry. Personal field values and
page dumps are excluded from the worker result. Temp PDFs are removed on exit.

Offline tests exercise package wiring and durable duplicate prevention. They do
not establish compatibility with the current live Comeet form. That requires
an authorized nonproduction browser test before activation.

## Unmerged pull request review

Reviewed PR [#1](https://github.com/assafep/ai-job-search-system/pull/1)
and [#2](https://github.com/assafep/ai-job-search-system/pull/2).
Neither PR reports executed live browser validation.

| Asset | Disposition |
| --- | --- |
| #1 exact role link without Senior assumption | Included in restored worker |
| #1 mark attempt before awaiting click; post-click uncertainty | Included |
| #1 required and aria-required field detection | Included; unfamiliar controls block |
| #1 combined PDF in both upload fields | Preserved |
| #1 durable worker ownership and old stage-task services | Current GDTQ claims/checkpoints retained; retired services not imported |
| #2 pre/post-click receipt comparison | Included; pre-existing confirmation text cannot count as new acceptance |
| #2 frame inventory, upload verification, consent checks | Used as review criteria; worker remains specific to known controls |
| #2 optional inspect.py and old Application Service skill references | Not restored: no current caller and obsolete runtime references |

Before live validation, inventory the actual rendered controls and frame origins,
verify both file uploads, and confirm an observed new receipt. Test missing
answers, unfamiliar consent controls, challenges and interrupted clicks in an
isolated browser fixture. Static checks and mocked Python tests do not replace
that browser-level validation.

## Package boundaries

Runtime code uses `ajss.submission.processing` for workflow decisions and
`ajss.submission.wiring` for backend composition. The adapter package owns
submission contracts and provider-specific I/O. Python launches `browser.cjs`
relative to `adapter.py`; there is no standalone browser entry point in the CLI.

Compatibility imports remain at `ajss.worker.stages.submit_prepared`,
`ajss.submission`, `adapters.job_boards.submission`, and
`adapters.job_boards.comeet_submit.submit`. These re-export the same functions;
no implementation is duplicated. New callers should use the implementation
modules shown above. Compatibility imports are covered by regression tests.
