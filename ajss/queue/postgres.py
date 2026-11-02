"""Neon/PostgreSQL index implementing the WorkIndex interface.

Statements live in ajss.queue.schema. This class opens transactions only.
The history backfill is sql/gdtq/001_neon_index.sql.
Never auto-create production tables from a request handler.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone,timedelta
from contextlib import contextmanager
from ajss.queue.identity import historical_keys, canonical_legacy_url
from ajss.queue.schema import (
    claim_item,
    expired_claims as read_expired_claims,
    identity_owners,
    insert_event,
    insert_identity,
    lock_identity_keys,
    read_checkpoint,
    read_claim,
    read_events,
    release_claim,
    write_checkpoint,
)


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

    def lookup_identity(self,company,role,url="",source_id=""):
        """Read the current owner. Does not add a row or take a write lock."""
        keys=historical_keys(company,role,url,source_id)
        if not keys:return None
        with self._tx() as conn:
            with conn.cursor() as cur:
                owners=identity_owners(cur,keys)
                return sorted(owners)[0] if owners else None

    def reserve_identity(self,item_id,company,role,url="",source_id=""):
        keys=historical_keys(company,role,url,source_id)
        if not keys:raise ValueError("Cannot identify posting")
        with self._tx() as conn:
            with conn.cursor() as cur:
                # Lock all keys in stable order to serialize competing writers,
                # including inserts when the key does not exist yet.
                lock_identity_keys(cur,keys)
                owners=identity_owners(cur,keys)
                if owners and owners!={item_id}:
                    return sorted(owners - {item_id})[0]
                for kind,key in keys:
                    insert_identity(cur,kind,key,item_id)
                return None

    def checkpoint(self,name,value=None):
        with self._tx() as conn:
            with conn.cursor() as cur:
                if value is None:
                    return read_checkpoint(cur,name)
                write_checkpoint(cur,name,json.dumps(value))
                return value

    def append_event(self,event):
        with self._tx() as conn:
            with conn.cursor() as cur:
                insert_event(cur,event["event_id"],event["item_id"],json.dumps(event,sort_keys=True))

    def events(self,item_id):
        with self._tx() as conn:
            with conn.cursor() as cur:
                return read_events(cur,item_id)

    def claim(self,item_id,source,attempt_id,lease_seconds=3600):
        now=datetime.now(timezone.utc)
        until=now+timedelta(seconds=lease_seconds)
        with self._tx() as conn:
            with conn.cursor() as cur:
                return claim_item(cur,item_id,source,attempt_id,until,now)

    def current_claim(self,item_id):
        with self._tx() as conn:
            with conn.cursor() as cur:
                return read_claim(cur,item_id)

    def release(self,item_id,attempt_id):
        with self._tx() as conn:
            with conn.cursor() as cur:
                return release_claim(cur,item_id,attempt_id)

    def expired_claims(self):
        with self._tx() as conn:
            with conn.cursor() as cur:
                return read_expired_claims(cur)

    def close(self):
        self._closed=True
