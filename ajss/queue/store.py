"""Durable SQLite index: deduplication, leases, and round-robin checkpoint."""
from __future__ import annotations
import json
import sqlite3
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from datetime import datetime, timezone, timedelta

from ajss.queue.identity import normalize, canonical_legacy_url as canonical_url, historical_keys
from ajss.queue.schema import (
    SQLITE_DDL,
    SQLITE_EXPIRED,
    SQLITE_INSERT_EVENT,
    SQLITE_INSERT_IDENTITY,
    SQLITE_RELEASE,
    SQLITE_SELECT_CHECKPOINT,
    SQLITE_SELECT_CLAIM,
    SQLITE_SELECT_EVENTS,
    SQLITE_SELECT_LEASE,
    SQLITE_SELECT_OWNER,
    SQLITE_UPSERT_CHECKPOINT,
    SQLITE_UPSERT_ITEM,
)

def posting_keys(company, role, url="", source_id=""):
    return [kind+":"+key for kind,key in historical_keys(company,role,url,source_id)]

def identity_keys(company, role, url="", source_id=""):
    return list(dict.fromkeys(posting_keys(company, role, url, source_id) +
                             _sqlite_legacy_keys(company, role, url, source_id)))

def _sqlite_legacy_url(url: str) -> str:
    u = urlsplit((url or "").strip())
    query = urlencode(sorted((k,v) for k,v in parse_qsl(u.query) if not k.lower().startswith("utm_")))
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"), query, ""))

def _sqlite_legacy_keys(company: str, role: str, url: str = "", source_id: str = "") -> list[str]:
    keys = []
    if source_id:
        keys.append("source:" + normalize(source_id))
    if url:
        keys.append("url:" + _sqlite_legacy_url(url))
    if company and role:
        keys.append("company_role:" + normalize(company) + "|" + normalize(role))
    return keys

class WorkIndex:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SQLITE_DDL)

    def _owners(self, keys):
        matches = [self.db.execute(SQLITE_SELECT_OWNER, (key,)).fetchone() for key in keys]
        return {row["item_id"] for row in matches if row}

    def lookup_identity(self, company, role, url="", source_id=""):
        """Read the current owner. Does not add a row or take a write lock."""
        keys = identity_keys(company, role, url, source_id)
        if not keys:
            return None
        with self.lock:
            owners = self._owners(keys)
            return sorted(owners)[0] if owners else None

    def reserve_identity(self, item_id, company, role, url="", source_id=""):
        keys = identity_keys(company, role, url, source_id)
        if not keys:
            raise ValueError("Cannot deduplicate a posting without identity")
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                owners = self._owners(keys)
                if owners and owners != {item_id}:
                    self.db.execute("ROLLBACK")
                    return sorted(owners - {item_id})[0]
                now = datetime.now(timezone.utc).isoformat()
                for key in keys:
                    self.db.execute(SQLITE_INSERT_IDENTITY,(key,item_id,now))
                self.db.execute("COMMIT")
                return None
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def checkpoint(self, name, value=None):
        with self.lock:
            if value is None:
                row=self.db.execute(SQLITE_SELECT_CHECKPOINT, (name,)).fetchone()
                return json.loads(row["value"]) if row else None
            self.db.execute(SQLITE_UPSERT_CHECKPOINT, (name,json.dumps(value)))
            return value

    def append_event(self, event: dict):
        with self.lock:
            self.db.execute(SQLITE_INSERT_EVENT, (event["event_id"],event["item_id"],json.dumps(event,sort_keys=True)))

    def events(self, item_id):
        return [json.loads(x["event_json"]) for x in self.db.execute(SQLITE_SELECT_EVENTS,(item_id,))]

    def claim(self, item_id, source, attempt_id, lease_seconds=3600):
        now=datetime.now(timezone.utc)
        until=(now+timedelta(seconds=lease_seconds)).isoformat()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row=self.db.execute(SQLITE_SELECT_LEASE, (item_id,)).fetchone()
                if row and row["attempt_id"] and row["attempt_id"]!=attempt_id:
                    self.db.execute("ROLLBACK")
                    return False
                self.db.execute(SQLITE_UPSERT_ITEM,
                     (item_id,source,attempt_id,until,now.isoformat()))
                self.db.execute("COMMIT")
                return True
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def current_claim(self,item_id):
        with self.lock:
            row=self.db.execute(SQLITE_SELECT_CLAIM,(item_id,)).fetchone()
            return dict(row) if row else None

    def release(self, item_id, attempt_id):
        with self.lock:
            return self.db.execute(SQLITE_RELEASE, (item_id,attempt_id)).rowcount == 1

    def expired_claims(self):
        return [dict(row) for row in self.db.execute(SQLITE_EXPIRED,(datetime.now(timezone.utc).isoformat(),))]

    def close(self):
        self.db.close()
