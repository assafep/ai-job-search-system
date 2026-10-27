"""Pure migration/key-compatibility tests; no production Neon credentials needed."""
from gdtq.postgres_index import historical_keys,canonical_legacy_url
from pathlib import Path

def test_historical_keys_use_legacy_separator():
    assert ("company_role","acme / senior engineer") in historical_keys(
        " ACME ","Senior Engineer","https://jobs.example.com/1")

def test_legacy_url_preserves_meaningful_query_and_removes_tracking():
    assert canonical_legacy_url("HTTPS://JOBS.EXAMPLE.COM/jobs/123/?gh_jid=42&utm_source=mail#frag")==(
        "https://jobs.example.com/jobs/123?gh_jid=42")

def test_migration_keeps_legacy_and_all_historical_applications():
    sql=(Path(__file__).resolve().parents[1]/"sql/gdtq/001_neon_index.sql").read_text()
    assert "FROM public.posting_keys" in sql
    assert "FROM public.applications" in sql
    assert "DROP " not in sql.upper()
    assert "missing_count<>0" in sql

def test_claims_are_atomic_and_checkpoint_json():
    from gdtq.postgres_index import PostgresWorkIndex
    import inspect
    source=inspect.getsource(PostgresWorkIndex)
    assert "ON CONFLICT (item_id) DO UPDATE" in source
    assert "RETURNING item_id" in source
    assert "pg_advisory_xact_lock" in source

def test_backfill_does_not_index_internal_urn_as_url():
    sql=(Path(__file__).resolve().parents[1]/"sql/gdtq/001_neon_index.sql").read_text()
    assert "posting_url ~* '^https?://'" in sql
    assert "FROM public.posting_keys" in sql
