"""Queue tables and the statements that read or write them.

Postgres and SQLite interactions live here. Onboarding creates the empty
Postgres tables when one is missing. A request handler does not. Copying
application history stays in sql/gdtq/001_neon_index.sql.

The worker writes public.gdtq_checkpoints before it takes the next queue item.
A missing checkpoints table is what stopped the Bookmarked run.
"""
from __future__ import annotations

# Columns the runtime statements use. Order is the sanity-check order.
REQUIRED_COLUMNS = {
    "gdtq_identities": ("key_type", "lookup_key", "application_id", "origin", "created_at"),
    "gdtq_items": ("item_id", "source_state", "attempt_id", "lease_until", "updated_at"),
    "gdtq_checkpoints": ("name", "value"),
    "gdtq_event_log": ("event_id", "item_id", "event_json", "inserted_at"),
    "posting_keys": ("key_type", "lookup_key", "application_id"),
}

EXPECTED_TABLES = tuple(REQUIRED_COLUMNS)

SQLITE_COLUMNS = {
    "identities": ("key", "item_id", "created_at"),
    "items": ("item_id", "source_state", "attempt_id", "lease_until", "updated_at"),
    "checkpoints": ("name", "value"),
    "event_log": ("event_id", "item_id", "event_json"),
}

# Empty tables only. CREATE IF NOT EXISTS leaves an existing table, including
# a historical posting_keys table, unchanged and copies no rows.
QUEUE_DDL = (
    """CREATE TABLE IF NOT EXISTS public.gdtq_identities(
  key_type text NOT NULL CHECK (key_type IN ('url','company_role','source')),
  lookup_key text NOT NULL,
  application_id text NOT NULL,
  origin text NOT NULL DEFAULT 'gdtq' CHECK (origin IN ('legacy','backfill','gdtq')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(key_type,lookup_key)
)""",
    """CREATE INDEX IF NOT EXISTS gdtq_identities_application_idx
  ON public.gdtq_identities(application_id)""",
    """CREATE TABLE IF NOT EXISTS public.gdtq_items(
  item_id text PRIMARY KEY,
  source_state text NOT NULL,
  attempt_id text,
  lease_until timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
)""",
    """CREATE INDEX IF NOT EXISTS gdtq_items_expired_idx
  ON public.gdtq_items(lease_until) WHERE attempt_id IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS public.gdtq_checkpoints(
  name text PRIMARY KEY,
  value jsonb NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS public.gdtq_event_log(
  event_id text PRIMARY KEY,
  item_id text NOT NULL,
  event_json jsonb NOT NULL,
  inserted_at timestamptz NOT NULL DEFAULT now()
)""",
    """CREATE INDEX IF NOT EXISTS gdtq_event_log_item_idx
  ON public.gdtq_event_log(item_id,inserted_at)""",
    """CREATE TABLE IF NOT EXISTS public.posting_keys(
  key_type text NOT NULL,
  lookup_key text NOT NULL,
  application_id text NOT NULL,
  PRIMARY KEY(key_type,lookup_key)
)""",
)

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS identities (
  key TEXT PRIMARY KEY, item_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
  item_id TEXT PRIMARY KEY, source_state TEXT NOT NULL,
  attempt_id TEXT, lease_until TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event_log (
  event_id TEXT PRIMARY KEY, item_id TEXT NOT NULL, event_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_item ON event_log(item_id);
"""

COLUMN_PRESENCE = (
    "SELECT table_name, column_name FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = ANY(%s)"
)
TABLE_PRESENCE = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = 'public' AND table_name = ANY(%s)"
)

SELECT_GDTQ_IDENTITY = (
    "SELECT application_id FROM public.gdtq_identities "
    "WHERE key_type=%s AND lookup_key=%s"
)
SELECT_POSTING_KEY = (
    "SELECT application_id FROM public.posting_keys "
    "WHERE key_type=%s AND lookup_key=%s"
)
SELECT_IDENTITY_WITH_ORIGIN = """SELECT application_id,origin FROM public.gdtq_identities
                                   WHERE key_type=%s AND lookup_key=%s
                                   UNION
                                   SELECT application_id,'legacy' FROM public.posting_keys
                                   WHERE key_type=%s AND lookup_key=%s"""
LOCK_IDENTITY_KEY = "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))"
INSERT_IDENTITY = """INSERT INTO public.gdtq_identities
                        (key_type,lookup_key,application_id,origin)
                        VALUES (%s,%s,%s,'gdtq')
                        ON CONFLICT (key_type,lookup_key) DO NOTHING"""
SELECT_CHECKPOINT = "SELECT value FROM public.gdtq_checkpoints WHERE name=%s"
UPSERT_CHECKPOINT = """INSERT INTO public.gdtq_checkpoints(name,value) VALUES (%s,%s::jsonb)
                    ON CONFLICT (name) DO UPDATE SET value=EXCLUDED.value"""
INSERT_EVENT = """INSERT INTO public.gdtq_event_log(event_id,item_id,event_json)
                    VALUES (%s,%s,%s::jsonb) ON CONFLICT (event_id) DO NOTHING"""
SELECT_EVENTS = (
    "SELECT event_json FROM public.gdtq_event_log "
    "WHERE item_id=%s ORDER BY inserted_at,event_id"
)
CLAIM_ITEM = """INSERT INTO public.gdtq_items
                    (item_id,source_state,attempt_id,lease_until,updated_at)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (item_id) DO UPDATE
                    SET source_state=EXCLUDED.source_state,
                        attempt_id=EXCLUDED.attempt_id,
                        lease_until=EXCLUDED.lease_until,
                        updated_at=EXCLUDED.updated_at
                    WHERE gdtq_items.attempt_id IS NULL
                      OR gdtq_items.attempt_id=EXCLUDED.attempt_id
                    RETURNING item_id"""
SELECT_CLAIM = "SELECT source_state,attempt_id FROM public.gdtq_items WHERE item_id=%s"
RELEASE_CLAIM = """UPDATE public.gdtq_items SET attempt_id=NULL,lease_until=NULL
                    WHERE item_id=%s AND attempt_id=%s RETURNING item_id"""
SELECT_EXPIRED = """SELECT item_id,source_state,attempt_id,lease_until,updated_at
                    FROM public.gdtq_items WHERE attempt_id IS NOT NULL AND lease_until < now()"""
QUEUE_COUNTS = """SELECT
                  (SELECT count(*) FROM public.gdtq_identities) AS identities,
                  (SELECT count(*) FROM public.gdtq_items WHERE attempt_id IS NOT NULL
                     AND lease_until>now()) AS active_leases,
                  (SELECT count(*) FROM public.gdtq_event_log) AS events,
                  (SELECT count(*) FROM public.gdtq_checkpoints) AS checkpoints"""

PROBE_IDENTITIES = "SELECT application_id FROM public.gdtq_identities LIMIT 0"
PROBE_POSTING_KEYS = "SELECT application_id FROM public.posting_keys LIMIT 0"
DELETE_EVENTS = "DELETE FROM public.gdtq_event_log WHERE item_id=%s"
DELETE_ITEMS = "DELETE FROM public.gdtq_items WHERE item_id=%s"
DELETE_IDENTITIES = "DELETE FROM public.gdtq_identities WHERE application_id=%s"
DELETE_ROUND_ROBIN = "DELETE FROM public.gdtq_checkpoints WHERE name='round_robin_cursor'"

SQLITE_SELECT_OWNER = "SELECT item_id FROM identities WHERE key=?"
SQLITE_INSERT_IDENTITY = "INSERT OR IGNORE INTO identities(key,item_id,created_at) VALUES(?,?,?)"
SQLITE_SELECT_CHECKPOINT = "SELECT value FROM checkpoints WHERE name=?"
SQLITE_UPSERT_CHECKPOINT = (
    "INSERT INTO checkpoints(name,value) VALUES(?,?) "
    "ON CONFLICT(name) DO UPDATE SET value=excluded.value"
)
SQLITE_INSERT_EVENT = "INSERT OR IGNORE INTO event_log(event_id,item_id,event_json) VALUES(?,?,?)"
SQLITE_SELECT_EVENTS = "SELECT event_json FROM event_log WHERE item_id=? ORDER BY rowid"
SQLITE_SELECT_LEASE = "SELECT attempt_id,lease_until FROM items WHERE item_id=?"
SQLITE_UPSERT_ITEM = """INSERT INTO items(item_id,source_state,attempt_id,lease_until,updated_at)
                     VALUES(?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET
                     source_state=excluded.source_state,attempt_id=excluded.attempt_id,
                     lease_until=excluded.lease_until,updated_at=excluded.updated_at"""
SQLITE_SELECT_CLAIM = "SELECT source_state,attempt_id FROM items WHERE item_id=?"
SQLITE_RELEASE = "UPDATE items SET attempt_id=NULL,lease_until=NULL WHERE item_id=? AND attempt_id=?"
SQLITE_EXPIRED = "SELECT * FROM items WHERE attempt_id IS NOT NULL AND lease_until < ?"


def gaps_from_columns(rows):
    """Names of missing tables, or table.column when the table exists but a column does not."""
    present = {}
    for table, column in rows:
        present.setdefault(table, set()).add(column)
    gaps = []
    for table, columns in REQUIRED_COLUMNS.items():
        found = present.get(table)
        if not found:
            gaps.append(table)
            continue
        for column in columns:
            if column not in found:
                gaps.append(table + "." + column)
    return gaps


def gaps_sentence(gaps, *, local=False):
    subject = "The local database is missing " if local else "The database is missing "
    return subject + ", ".join(gaps) + "."


def schema_gaps(cur):
    cur.execute(COLUMN_PRESENCE, (list(EXPECTED_TABLES),))
    return gaps_from_columns(cur.fetchall())


def sqlite_gaps(conn):
    gaps = []
    for table, columns in SQLITE_COLUMNS.items():
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not found:
            gaps.append(table)
            continue
        info = conn.execute("PRAGMA table_info(" + table + ")").fetchall()
        names = {row[1] for row in info}
        for column in columns:
            if column not in names:
                gaps.append(table + "." + column)
    return gaps


def identity_owners(cur, keys):
    owners = set()
    for kind, key in keys:
        cur.execute(SELECT_GDTQ_IDENTITY, (kind, key))
        row = cur.fetchone()
        if row:
            owners.add(row[0])
        if kind in {"url", "company_role"}:
            cur.execute(SELECT_POSTING_KEY, (kind, key))
            row = cur.fetchone()
            if row:
                owners.add(row[0])
    return owners


def lock_identity_keys(cur, keys):
    for kind, key in sorted(set(keys)):
        cur.execute(LOCK_IDENTITY_KEY, (kind + ":" + key,))


def insert_identity(cur, kind, key, item_id):
    cur.execute(INSERT_IDENTITY, (kind, key, item_id))


def identity_matches(cur, keys):
    matches = []
    for kind, key in keys:
        cur.execute(SELECT_IDENTITY_WITH_ORIGIN, (kind, key, kind, key))
        for row in cur.fetchall():
            matches.append({"key_type": kind, "application_id": row[0], "origin": row[1]})
    return matches


def read_checkpoint(cur, name):
    cur.execute(SELECT_CHECKPOINT, (name,))
    row = cur.fetchone()
    return row[0] if row else None


def write_checkpoint(cur, name, value):
    cur.execute(UPSERT_CHECKPOINT, (name, value))


def insert_event(cur, event_id, item_id, event_json):
    cur.execute(INSERT_EVENT, (event_id, item_id, event_json))


def read_events(cur, item_id):
    cur.execute(SELECT_EVENTS, (item_id,))
    return [row[0] for row in cur.fetchall()]


def claim_item(cur, item_id, source, attempt_id, until, now):
    cur.execute(CLAIM_ITEM, (item_id, source, attempt_id, until, now))
    return cur.fetchone() is not None


def read_claim(cur, item_id):
    cur.execute(SELECT_CLAIM, (item_id,))
    row = cur.fetchone()
    return {"source_state": row[0], "attempt_id": row[1]} if row else None


def release_claim(cur, item_id, attempt_id):
    cur.execute(RELEASE_CLAIM, (item_id, attempt_id))
    return cur.fetchone() is not None


def expired_claims(cur):
    cur.execute(SELECT_EXPIRED)
    return [
        dict(zip(("item_id", "source_state", "attempt_id", "lease_until", "updated_at"), row))
        for row in cur.fetchall()
    ]


def queue_counts(cur):
    cur.execute(QUEUE_COUNTS)
    row = cur.fetchone()
    return dict(zip(("identities", "active_leases", "events", "checkpoints"), row))
