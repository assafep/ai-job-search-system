from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Application:
    application_id: str
    company: str
    role: str
    posting_url: str
    status: str = "Bookmarked"
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    next_action: Optional[str] = None
    next_action_at: Optional[str] = None
    artifact_ids: List[str] = field(default_factory=list)
    interview_ids: List[str] = field(default_factory=list)
    last_event_id: Optional[str] = None

    @classmethod
    def create(cls, company: str, role: str, posting_url: str) -> "Application":
        return cls("app_" + uuid.uuid4().hex, company, role, posting_url)


@dataclass
class Event:
    event_id: str
    application_id: str
    event_type: str
    occurred_at: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Artifact:
    artifact_id: str
    application_id: str
    name: str
    drive_file_id: Optional[str] = None
    mime_type: Optional[str] = None
    created_at: str = field(default_factory=now)
