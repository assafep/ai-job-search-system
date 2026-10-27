# Testing

## GDTQ (current)

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
python -m gdtq.cli doctor
PYTHONPATH=. python -m pytest -q tests/test_gdtq.py
```

This test suite uses fake Google Docs and Drive implementations, SQLite temporary databases, and fake Gmail/Calendar adapters. It covers legal transitions/exhaustive code mapping, wrong-item claims, in-flight moves, retry backoff, deduplication, terminal archive verification, manual updates, observer idempotency, PDF/report timing, and explicit Gmail send permission. No live credentials or website submits are required. GitHub Actions runs the focused suite via [`gdtq-tests.yml`](../.github/workflows/gdtq-tests.yml).

## Legacy regression suite

The other `tests/test_*.py` files exercise the previous Application Service, stage-task, provisioning and ATS framework. They remain valuable while their code is still imported or deployed. Use `python -m pytest -q` (with all project requirements installed) when testing compatibility changes.

Do **not** treat unit tests as certification of live Google Docs tab reparenting, Gmail OAuth permission, external submission confirmation or concurrent multi-process writers. Perform a separate integration pass using a non-production Google document before switching runtimes.

The retired Jobify/RVC/EchoJobs browser-trial fixture test and its experimental helper were removed together, rather than leaving an orphaned test targeting absent site-recipes JSON. Existing ATS adapter and submission safety tests remain.

The obsolete `test_posting_fetcher.py` fixture suite was also retired: it imported the already-removed `services.posting_fetcher` module and depended on the experimental `scrapling` implementation. Current posting behavior is exercised by `test_job_board_framework.py`, `test_posting_details.py` and GDTQ tests.
