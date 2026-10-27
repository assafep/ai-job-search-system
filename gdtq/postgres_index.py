"""Neon/PostgreSQL index implementing the GDTQ WorkIndex interface.

The database schema is installed separately using sql/gdtq/001_neon_index.sql.
Never auto-create production tables from a request handler.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone,timedelta
from contextlib import contextmanager
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from .store import normalize

TRACKING_KEYS=frozenset(("utm_source","utm_medium","utm_campaign","utm_term","utm_content","fbclid","gclid"))

def canonical_legacy_url(value):
    parsed=urlsplit((value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return (value or "").strip()
    query=urlencode([(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True)
                     if k.lower() not in TRACKING_KEYS])
    return urlunsplit((parsed.scheme.lower(),parsed.netloc.lower(),parsed.path.rstrip("/") or "/",query,""))

def historical_keys(company,role,url="",source_id=""):
    """The old posting_keys index used 'company / role' and normalized URLs.

    Preserve that format so the historical entries are matched without scanning
    the Drive application directory or reapplying to already completed jobs.
    """
    keys=[]
    if source_id:keys.append(("source",normalize(source_id)))
    if url:keys.append(("url",canonical_legacy_url(url)))
    if company and role:keys.append(("company_role",normalize(company)+" / "+normalize(role)))
    return keys

class PostgresWorkIndex:
    def __init__(self,dsn=None,*,connect=None):
        if connect is None:
            if not dsn:raise ValueError("DATABASE_URL is required for Neon WorkIndex")
            try:import psycopg
            except ImportError as exc:raise RuntimeError("Install psycopg[binary] to use Neon") from exc
            self._connect=lambda:psycopg.connect(dsn,connect_timeout=10)
        else:self._connect=connect
        self._closed=False

    @contextmanager
    def _tx(self):
        if self._closed:raise RuntimeError("WorkIndex is closed")
        connection=self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def reserve_identity(self,item_id,company,role,url="",source_id=""):
        keys=historical_keys(company,role,url,source_id)
        if not keys:raise ValueError("Cannot identify posting")
        with self._tx() as conn:
            with conn.cursor() as cur:
                # Lock all keys in stable order to serialize competing writers,
                # including inserts when the key does not exist yet.
                for kind,key in sorted(set(keys)):
                    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(kind+":"+key,))
                owners=set()
                for kind,key in keys:
                    cur.execute("SELECT application_id FROM public.gdtq_identities WHERE key_type=%s AND lookup_key=%s",(kind,key))
                    row=cur.fetchone()
                    if row:owners.add(row[0])
                if owners and owners!={item_id}:
                    return sorted(owners)[0]
                for kind,key in keys:
                    cur.execute("""INSERT INTO public.gdtq_identities
                        (key_type,lookup_key,application_id,origin)
                        VALUES (%s,%s,%s,'gdtq')
                        ON CONFLICT (key_type,lookup_key) DO NOTHING""",(kind,key,item_id))
                return None

    def checkpoint(self,name,value=None):
        with self._tx() as conn:
            with conn.cursor() as cur:
                if value is None:
                    cur.execute("SELECT value FROM public.gdtq_checkpoints WHERE name=%s",(name,))
                    row=cur.fetchone()
                    return row[0] if row else None
                cur.execute("""INSERT INTO public.gdtq_checkpoints(name,value) VALUES (%s,%s::jsonb)
                    ON CONFLICT (name) DO UPDATE SET value=EXCLUDED.value""",(name,json.dumps(value)))
                return value

    def append_event(self,event):
        with self._tx() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO public.gdtq_event_log(event_id,item_id,event_json)
                    VALUES (%s,%s,%s::jsonb) ON CONFLICT (event_id) DO NOTHING""",
                    (event["event_id"],event["item_id"],json.dumps(event,sort_keys=True)))

    def events(self,item_id):
        with self._tx() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT event_json FROM public.gdtq_event_log WHERE item_id=%s ORDER BY inserted_at,event_id",(item_id,))
                return [row[0] for row in cur.fetchall()]

    def claim(self,item_id,source,attempt_id,lease_seconds=3600):
        now=datetime.now(timezone.utc)
        until=now+timedelta(seconds=lease_seconds)
        with self._tx() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO public.gdtq_items
                    (item_id,source_state,attempt_id,lease_until,updated_at)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (item_id) DO UPDATE
                    SET source_state=EXCLUDED.source_state,
                        attempt_id=EXCLUDED.attempt_id,
                        lease_until=EXCLUDED.lease_until,
                        updated_at=EXCLUDED.updated_at
                    WHERE gdtq_items.attempt_id IS NULL
                      OR gdtq_items.attempt_id=EXCLUDED.attempt_id
                      OR gdtq_items.lease_until <= EXCLUDED.updated_at
                    RETURNING item_id""",(item_id,source,attempt_id,until,now))
                return cur.fetchone() is not None

    def release(self,item_id,attempt_id):
        with self._tx() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE public.gdtq_items SET attempt_id=NULL,lease_until=NULL
                    WHERE item_id=%s AND attempt_id=%s RETURNING item_id""",(item_id,attempt_id))
                return cur.fetchone() is not None

    def expired_claims(self):
        with self._tx() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT item_id,source_state,attempt_id,lease_until,updated_at
                    FROM public.gdtq_items WHERE attempt_id IS NOT NULL AND lease_until < now()""")
                return [dict(zip(("item_id","source_state","attempt_id","lease_until","updated_at"),row))
                        for row in cur.fetchall()]

    def close(self):
        self._closed=True
