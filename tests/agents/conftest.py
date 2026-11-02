"""Isolation rules apply only to the agent unit suite."""
from copy import deepcopy
from datetime import datetime, date, timezone
import socket
import sqlite3
import subprocess
from unittest.mock import Mock
import pytest

FIXED_NOW = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolate_agents(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Agent unit test attempted real network, database or subprocess I/O")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    try:
        import psycopg
    except ImportError:
        pass
    else:
        monkeypatch.setattr(psycopg, "connect", forbidden)
    for name in ("DATABASE_URL", "AJSS_DATABASE_URL", "OPENAI_API_KEY", "XAI_API_KEY",
                 "AJSS_LLM_API_KEY", "AJSS_LLM_PROVIDER", "AJSS_LLM_MODEL",
                 "AJSS_SUBMISSION_BACKEND", "AJSS_ENABLE_DISPATCH", "AJSS_GMAIL_ALLOW_SEND"):
        monkeypatch.delenv(name, raising=False)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_NOW.astimezone(tz) if tz else FIXED_NOW.replace(tzinfo=None)
    class FixedDate(date):
        @classmethod
        def today(cls):return FIXED_NOW.date()
    monkeypatch.setattr("ajss.worker.observers.datetime", FixedDatetime)
    monkeypatch.setattr("ajss.worker.observers.date", FixedDate)


@pytest.fixture
def item():
    return {"item_id":"item-1", "company":"Example Labs", "role":"Engineer",
            "posting_url":"https://jobs.example.test/123", "description":"Python " * 100}


@pytest.fixture
def index():
    values = {}
    def checkpoint(name, value=None):
        if value is not None:values[name] = deepcopy(value)
        return deepcopy(values.get(name))
    result = Mock(spec_set=["checkpoint", "reserve_identity", "lookup_identity", "events"])
    result.checkpoint.side_effect = checkpoint
    result.reserve_identity.return_value = None
    result.lookup_identity.return_value = None
    result.events.return_value = []
    return result


@pytest.fixture
def applications():
    return [{"application_id":"item-1", "company":"Example Labs", "role":"Engineer", "status":"Applied"}]
