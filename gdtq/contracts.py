"""Shared event contracts; these objects can be passed between providers unchanged."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass(frozen=True)
class Event:
    item_id: str
    event_type: str
    previous_state: str
    current_state: str
    processor: str
    outcome: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)
    rules_version: str = "1"
    attempt_id: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

@dataclass(frozen=True)
class Claimed:
    item_id: str
    source_state: str
    latest_event: dict | None
    document_id: str
    tab_url: str
    attempt_id: str

@dataclass(frozen=True)
class Decision:
    source_state: str
    next_state: str
    terminal: bool
    outcome_code: str
    rules_version: str

@dataclass(frozen=True)
class ProcessingOutcome:
    """Business outcome is separate from execution status."""
    agent: str
    item_id: str
    code: str
    execution_status: str = "SUCCESS"
    summary: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    attempt_id: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)
