# Real Google Docs + PostgreSQL integration tests

These tests exercise the actual GDTQ queue, orchestrator, policy, Google Docs
client and PostgreSQL index. Fixed stage outcomes replace LLM/browser actions.
They are storage/workflow integration tests, not full external submission tests.

Coverage: both flight modes, competing claims, stale outcomes in database mode,
four-stage transitions through Applied, matching document/database event IDs,
event replay, duplicate URLs/company-role, retry backoff and concurrent identity
reservation. No employer site, mailbox, model or applicant data is used.

Default `pytest -q tests/` skips these four cases. Explicit execution requires:

- `AJSS_TEST_DATABASE_URL`: connection string for a dedicated nonproduction Neon
  branch with the deployed AJSS schema (`ajss-db`). Never use main.
- `AJSS_TEST_DATABASE_HOST`: independently verify this endpoint belongs to that
  test branch. The fixture checks the DSN matches this hostname.
- `AJSS_TEST_BRANCH_ID`: verified test branch ID; known main is refused.
- Google OAuth credentials supported by `adapters/google/auth.py`, with Docs and
  Drive access. Each test creates a fresh Google Doc, never reuses a user doc.

Use a private environment file outside Git; do not place secrets in commands:

```bash
docker compose --profile dev run --rm \
  --env-from-file /absolute/path/to/private-test.env \
  ajss-dev python -m pytest -q -s --tb=short tests/integration/ --live-integration
```

If your Compose version lacks `--env-from-file`, use a local Compose override
that adds this file to the dev service's `env_file` list. Do not print secrets.

By default newly created documents are moved to Trash in teardown. Set
`AJSS_KEEP_TEST_DOCUMENT=1` to retain them for inspection. Document links are
printed, and fixture-owned database rows are deleted by exact IDs. The prior
round-robin checkpoint is restored. Cleanup runs on failures too; abrupt process
termination may leave synthetic fixtures. Run only one integration session on
the dedicated branch because the scheduler checkpoint is shared. The branch is
not deleted by tests and may be reused after cleanup.

The test configuration is an operator assertion of nonproduction ownership;
the tests do not authenticate to Neon's control-plane API themselves. Provision
and verify the branch/endpoint before passing credentials.
