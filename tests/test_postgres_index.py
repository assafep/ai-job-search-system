"""Pure migration/key-compatibility tests; no production Neon credentials needed."""
from ajss.queue.postgres import historical_keys,canonical_legacy_url
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

def test_lookup_identity_does_not_write():
    from ajss.queue.postgres import PostgresWorkIndex
    import inspect
    source=inspect.getsource(PostgresWorkIndex.lookup_identity)
    assert "INSERT" not in source.upper()
    assert "pg_advisory_xact_lock" not in source


def test_claims_are_atomic_and_checkpoint_json():
    from ajss.queue import schema
    import inspect
    import re
    source=inspect.getsource(schema)
    assert "ON CONFLICT (item_id) DO UPDATE" in source
    assert "RETURNING item_id" in source
    assert "pg_advisory_xact_lock" in source
    referenced=set(re.findall(r"public\.([a-z_]+)", source))
    assert referenced == set(schema.REQUIRED_COLUMNS)
    assert "applications" not in source
    assert "completed_snapshots" not in source


def test_checkpoint_table_is_a_schema_gap_when_it_is_missing():
    from ajss.queue.schema import REQUIRED_COLUMNS, gaps_from_columns, gaps_sentence
    rows=[]
    for table, columns in REQUIRED_COLUMNS.items():
        if table == "gdtq_checkpoints":
            continue
        for column in columns:
            rows.append((table, column))
    assert gaps_from_columns(rows) == ["gdtq_checkpoints"]
    assert gaps_sentence(["gdtq_checkpoints"]) == "The database is missing gdtq_checkpoints."
    rows.append(("gdtq_checkpoints", "name"))
    assert gaps_from_columns(rows) == ["gdtq_checkpoints.value"]

def test_backfill_does_not_index_internal_urn_as_url():
    sql=(Path(__file__).resolve().parents[1]/"sql/gdtq/001_neon_index.sql").read_text()
    assert "posting_url ~* '^https?://'" in sql
    assert "FROM public.posting_keys" in sql
