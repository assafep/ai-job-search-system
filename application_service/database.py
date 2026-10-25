"""Runtime PostgreSQL connections; the application role never performs DDL."""
import os
from contextlib import contextmanager


def connect():
    import psycopg
    url = os.environ.get('AJSS_DATABASE_URL')
    if not url:
        raise RuntimeError('AJSS_DATABASE_URL is required')
    return psycopg.connect(url, autocommit=True, connect_timeout=15)


@contextmanager
def installation_lock(connection, identity):
    # One session lock also coordinates Drive writes across function instances.
    # Use the direct database URL; transaction pooling cannot hold session locks.
    connection.execute("SET statement_timeout = '90s'")
    connection.execute("SET lock_timeout = '30s'")
    connection.execute('SELECT pg_advisory_lock(hashtextextended(%s, 0))', (identity,))
    try:
        yield
    finally:
        connection.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', (identity,))
