# Tests

The current repository has one runtime: GDTQ.

```bash
python -m pip install -r requirements.txt pytest
python -m gdtq.cli doctor
python -m pytest -q tests/
```

Tests use injected fake Google Docs/Drive/mail resources and temporary SQLite. No live OAuth, website submission or mailbox side effects occur. `.github/workflows/gdtq-tests.yml` runs the complete retained suite. Any new integration must have deterministic unit tests and a separate opt-in live smoke test.
