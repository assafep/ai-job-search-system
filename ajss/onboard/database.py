"""Connect to the Postgres database chosen during onboarding.

The queue schema and its statements live in ajss.queue.schema. This module
opens the connection and creates any missing empty table. It does not copy
application history.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from ajss.queue.schema import EXPECTED_TABLES, QUEUE_DDL, TABLE_PRESENCE

__all__ = (
    "EXPECTED_TABLES",
    "QUEUE_DDL",
    "DatabaseCheckError",
    "ensure_queue_tables",
    "is_postgres_url",
)


class DatabaseCheckError(Exception):
    """The database could not be checked. The message never includes the URL."""

    def __init__(self):
        super().__init__("The database did not answer.")


def is_postgres_url(value):
    parts = urlsplit((value or "").strip())
    return parts.scheme in {"postgres", "postgresql"} and bool(parts.hostname)


def ensure_queue_tables(url, connect=None):
    """Return True when any expected table was missing and the statements ran."""
    if connect is None:
        def connect():
            import psycopg
            return psycopg.connect(url, connect_timeout=10)
    try:
        connection = connect()
    except Exception:
        raise DatabaseCheckError() from None
    try:
        with connection:
            with connection.cursor() as cur:
                cur.execute(TABLE_PRESENCE, (list(EXPECTED_TABLES),))
                found = {row[0] for row in cur.fetchall()}
                missing = [name for name in EXPECTED_TABLES if name not in found]
                if missing:
                    for statement in QUEUE_DDL:
                        cur.execute(statement)
        return bool(missing)
    except DatabaseCheckError:
        raise
    except Exception:
        raise DatabaseCheckError() from None
    finally:
        closer = getattr(connection, "close", None)
        if closer is not None:
            try:
                closer()
            except Exception:
                pass
