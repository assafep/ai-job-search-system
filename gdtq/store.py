"""Durable SQLite index: deduplication, leases, and round-robin checkpoint."""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from datetime import datetime, timezone, timedelta

def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()

def canonical_url(url: str) -> str:
    u = urlsplit((url or "").strip())
    query = urlencode(sorted((k,v) for k,v in parse_qsl(u.query) if not k.lower().startswith("utm_")))
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"), query, ""))

def posting_keys(company: str, role: str, url: str = "", source_id: str = "") -> list[str]:
    keys = []
    if source_id:
        keys.append("source:" + normalize(source_id))
    if url:
        keys.append("url:" + canonical_url(url))
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
        self.db.executescript("""
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
        """)

    def reserve_identity(self, item_id, company, role, url="", source_id=""):
        keys = posting_keys(company, role, url, source_id)
        if not keys:
            raise ValueError("Cannot deduplicate a posting without identity")
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                matches = [self.db.execute("SELECT item_id FROM identities WHERE key=?", (key,)).fetchone() for key in keys]
                owners = {row["item_id"] for row in matches if row}
                if owners and owners != {item_id}:
                    self.db.execute("ROLLBACK")
                    return sorted(owners)[0]
                now = datetime.now(timezone.utc).isoformat()
                for key in keys:
                    self.db.execute("INSERT OR IGNORE INTO identities(key,item_id,created_at) VALUES(?,?,?)",(key,item_id,now))
                self.db.execute("COMMIT")
                return None
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def checkpoint(self, name, value=None):
        with self.lock:
            if value is None:
                row=self.db.execute("SELECT value FROM checkpoints WHERE name=?", (name,)).fetchone()
                return json.loads(row["value"]) if row else None
            self.db.execute("INSERT INTO checkpoints(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name,json.dumps(value)))
            return value

    def append_event(self, event: dict):
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO event_log(event_id,item_id,event_json) VALUES(?,?,?)", (event["event_id"],event["item_id"],json.dumps(event,sort_keys=True)))

    def events(self, item_id):
        return [json.loads(x["event_json"]) for x in self.db.execute("SELECT event_json FROM event_log WHERE item_id=? ORDER BY rowid",(item_id,))]

    def claim(self, item_id, source, attempt_id, lease_seconds=3600):
        now=datetime.now(timezone.utc)
        until=(now+timedelta(seconds=lease_seconds)).isoformat()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row=self.db.execute("SELECT attempt_id,lease_until FROM items WHERE item_id=?", (item_id,)).fetchone()
                if row and row["attempt_id"] and row["lease_until"] and row["lease_until"]>now.isoformat() and row["attempt_id"]!=attempt_id:
                    self.db.execute("ROLLBACK")
                    return False
                self.db.execute("""INSERT INTO items(item_id,source_state,attempt_id,lease_until,updated_at)
                     VALUES(?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET
                     source_state=excluded.source_state,attempt_id=excluded.attempt_id,
                     lease_until=excluded.lease_until,updated_at=excluded.updated_at""",
                     (item_id,source,attempt_id,until,now.isoformat()))
                self.db.execute("COMMIT")
                return True
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def release(self, item_id, attempt_id):
        with self.lock:
            return self.db.execute("UPDATE items SET attempt_id=NULL,lease_until=NULL WHERE item_id=? AND attempt_id=?", (item_id,attempt_id)).rowcount == 1

    def expired_claims(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM items WHERE attempt_id IS NOT NULL AND lease_until < ?",(datetime.now(timezone.utc).isoformat(),))]

    def close(self):
        self.db.close()
