from typing import Dict, List, Optional
import uuid

from .models import Application, Artifact, Event, now
from .journal_format import ALL_STATES, normalize_posting_url
from services.activity_log import log_transition
from .profile import validate_profile
from .state_machine import validate_transition
from .file_paths import normalize_file_path


class Repository:
    """Persistence boundary. Agents and HTTP handlers must use this interface."""

    def create_application(self, company: str, role: str, posting_url: str, data=None, idempotency_key=None) -> Application:
        raise NotImplementedError

    def get_application(self, application_id: str) -> Optional[Application]:
        raise NotImplementedError

    def list_applications(self, status: Optional[str] = None) -> List[Application]:
        raise NotImplementedError

    def transition(self, application_id: str, target: str, data=None, idempotency_key=None) -> Application:
        raise NotImplementedError

    def append_event(self, application_id: str, event_type: str, data=None, idempotency_key=None) -> Event:
        raise NotImplementedError

    def list_events(self, application_id: str) -> List[Event]:
        raise NotImplementedError

    def save_artifact(self, application_id: str, name: str, drive_file_id=None, mime_type=None, idempotency_key=None) -> Artifact:
        raise NotImplementedError

    def store_artifact(self, application_id: str, name: str, content: bytes, mime_type=None, idempotency_key=None) -> Artifact:
        raise NotImplementedError

    def write_research(self, application_id: str, title: str, content: str, idempotency_key=None):
        raise NotImplementedError

    def list_research(self, application_id: str):
        raise NotImplementedError

    def list_artifacts(self, application_id: str):
        raise NotImplementedError

    def read_artifact(self, application_id: str, artifact_id: str):
        raise NotImplementedError

    def store_file(self, path: str, content: bytes, mime_type=None, idempotency_key=None):
        raise NotImplementedError

    def read_file(self, path: str):
        raise NotImplementedError

    def get_profile(self):
        raise NotImplementedError

    def save_profile(self, profile):
        raise NotImplementedError

    def journal_status(self):
        raise NotImplementedError

    def journal_tabs(self):
        raise NotImplementedError

    def queue_counts(self):
        counts = {state: 0 for state in ALL_STATES}
        for application in self.list_applications():
            status = application.status if hasattr(application, "status") else application["status"]
            counts[status] = counts.get(status, 0) + 1
        return counts

    def bookmark_staged_tab(self, application_id, idempotency_key):
        raise NotImplementedError

    def repair_journal(self):
        raise NotImplementedError


class MemoryRepository(Repository):
    """Runnable reference backend used for local development and contract tests."""

    def __init__(self):
        self.applications: Dict[str, Application] = {}
        self.events: Dict[str, Event] = {}
        self.artifacts: Dict[str, Artifact] = {}
        self.artifact_contents: Dict[str, bytes] = {}
        self.research = {}
        self.idempotency: Dict[str, object] = {}
        self.profile = None
        self.files = {}

    def _cached(self, key):
        return self.idempotency.get(key) if key else None

    def create_application(self, company, role, posting_url, data=None, idempotency_key=None):
        cached = self._cached(idempotency_key)
        if cached:
            return cached
        posting_url = normalize_posting_url(posting_url)
        for existing in self.applications.values():
            if normalize_posting_url(existing.posting_url) == posting_url:
                if idempotency_key:
                    self.idempotency[idempotency_key] = existing
                return existing
        app = Application.create(company, role, posting_url)
        self.applications[app.application_id] = app
        event_data = {
            "date": app.created_at[:10],
            "bullets": ["Posting URL: " + posting_url],
            "source": "data layer",
        }
        if data:
            event_data.update({key: value for key, value in data.items() if key not in {"date", "bullets"}})
            if data.get("bullets"):
                event_data["bullets"].extend(data["bullets"])
            if data.get("relevance_evidence"):
                event_data["bullets"].extend(
                    "Relevance: " + str(evidence) for evidence in data["relevance_evidence"])
        self.append_event(app.application_id, "application_created", event_data)
        if idempotency_key:
            self.idempotency[idempotency_key] = app
        return app

    def get_application(self, application_id):
        return self.applications.get(application_id)

    def list_applications(self, status=None):
        values = list(self.applications.values())
        return [x for x in values if status is None or x.status == status]

    def transition(self, application_id, target, data=None, idempotency_key=None):
        cached = self._cached(idempotency_key)
        if cached:
            return cached
        app = self.applications.get(application_id)
        if not app:
            raise KeyError(application_id)
        validate_transition(app.status, target)
        if app.status == target:
            if idempotency_key:
                self.idempotency[idempotency_key] = app
            return app
        # Keep the transition operation's idempotency record separate from its
        # journal event. If a process fails after the event is persisted but
        # before the state assignment, a retry must recover the event and still
        # return an Application rather than the cached Event object.
        event_key = (idempotency_key + ":status-event") if idempotency_key else None
        source = app.status
        # The caller explains the outcome. The status line is the last bullet
        # of that one event, not a second copy of the same note.
        detail = [str(item) for item in (data or {}).get("bullets") or [] if str(item).strip()]
        event = self.append_event(application_id, "status_changed", {
            "from": source, "to": target, "data": data or {},
            "bullets": detail + ["Moved from {} to {}".format(source, target)],
        }, event_key)
        app.status = target
        log_transition(app.company, app.role, source, target)
        app.updated_at = now()
        app.last_event_id = event.event_id
        if idempotency_key:
            self.idempotency[idempotency_key] = app
        return app

    def append_event(self, application_id, event_type, data=None, idempotency_key=None):
        cached = self._cached(idempotency_key)
        if cached:
            return cached
        if application_id not in self.applications:
            raise KeyError(application_id)
        event = Event("evt_" + uuid.uuid4().hex, application_id, event_type, now(), data or {})
        self.events[event.event_id] = event
        self.applications[application_id].last_event_id = event.event_id
        self.applications[application_id].updated_at = event.occurred_at
        if idempotency_key:
            self.idempotency[idempotency_key] = event
        return event

    def list_events(self, application_id):
        if application_id not in self.applications:
            raise KeyError(application_id)
        return [event for event in self.events.values() if event.application_id == application_id]

    def save_artifact(self, application_id, name, drive_file_id=None, mime_type=None):
        if application_id not in self.applications:
            raise KeyError(application_id)
        artifact = Artifact("artifact_" + uuid.uuid4().hex, application_id, name, drive_file_id, mime_type)
        self.artifacts[artifact.artifact_id] = artifact
        self.applications[application_id].artifact_ids.append(artifact.artifact_id)
        return artifact

    def get_profile(self):
        return self.profile

    def save_profile(self, profile):
        self.profile = validate_profile(profile)
        return profile

    def store_artifact(self, application_id, name, content, mime_type=None, idempotency_key=None):
        cached = self._cached(idempotency_key)
        if cached:
            return cached
        if application_id not in self.applications:
            raise KeyError(application_id)
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        artifact = self.save_artifact(
            application_id,
            name,
            "memory://" + uuid.uuid4().hex,
            mime_type,
        )
        self.artifact_contents[artifact.artifact_id] = content
        if idempotency_key:
            self.idempotency[idempotency_key] = artifact
        return artifact

    def write_research(self, application_id, title, content, idempotency_key=None):
        if application_id not in self.applications:
            raise KeyError(application_id)
        cached = self._cached(idempotency_key)
        if cached:
            return cached
        result = {
            "application_id": application_id,
            "title": title,
            "content": content,
            "research_id": "research_" + uuid.uuid4().hex,
        }
        self.research[result["research_id"]] = result
        if idempotency_key:
            self.idempotency[idempotency_key] = result
        return result

    def list_research(self, application_id):
        if application_id not in self.applications:
            raise KeyError(application_id)
        return [item for item in self.research.values() if item["application_id"] == application_id]

    def list_artifacts(self, application_id):
        if application_id not in self.applications:
            raise KeyError(application_id)
        return [artifact for artifact in self.artifacts.values() if artifact.application_id == application_id]

    def read_artifact(self, application_id, artifact_id):
        if application_id not in self.applications:
            raise KeyError(application_id)
        artifact = self.artifacts.get(artifact_id)
        if not artifact or artifact.application_id != application_id:
            raise KeyError(artifact_id)
        return {"artifact": artifact, "content": self.artifact_contents.get(artifact_id, b"")}

    def store_file(self, path, content, mime_type=None, idempotency_key=None):
        if not isinstance(content, bytes):
            raise TypeError("file content must be bytes")
        normalized = normalize_file_path(path)
        cached = self._cached(("file", idempotency_key)) if idempotency_key else None
        if cached:
            return cached
        result = {
            "file_id": "file_" + uuid.uuid4().hex,
            "path": normalized,
            "name": normalized.rsplit("/", 1)[-1],
            "mime_type": mime_type,
        }
        self.files[normalized] = {"file": result, "content": content}
        if idempotency_key:
            self.idempotency[("file", idempotency_key)] = result
        return result

    def read_file(self, path):
        normalized = normalize_file_path(path)
        if normalized not in self.files:
            raise KeyError(normalized)
        stored = self.files[normalized]
        return {"file": stored["file"], "content": stored["content"]}

    def journal_status(self):
        return {
            "valid": True,
            "required_states": [
                "Bookmarked", "Ready for Auto-Apply", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"
            ],
            "present_states": [
                "Bookmarked", "Ready for Auto-Apply", "Manual Apply Required", "Dead Letters Queue", "Applied", "Interviewing", "Rejected", "Offer"
            ],
            "missing_states": [],
            "duplicate_states": [],
        }

    def journal_tabs(self):
        """Expose the same tab shape the Google journal uses for operator staging."""
        states = list(ALL_STATES)
        if any(app.status == "Relay" for app in self.applications.values()):
            states.append("Relay")
        tabs = [{
            "tab_id": "state:" + state,
            "title": state,
            "parent_tab_id": None,
            "paragraphs": [],
        } for state in states]
        for app in self.applications.values():
            paragraphs = []
            for event in self.list_events(app.application_id):
                for bullet in (event.data or {}).get("bullets") or []:
                    paragraphs.append({"text": str(bullet)})
            if app.posting_url and not any(item["text"].startswith("Posting URL:") for item in paragraphs):
                paragraphs.insert(0, {"text": "Posting URL: " + app.posting_url})
            tabs.append({
                "tab_id": app.application_id,
                "title": "{} / {}".format(app.company, app.role),
                "parent_tab_id": "state:" + app.status,
                "paragraphs": paragraphs,
            })
        return tabs

    def bookmark_staged_tab(self, application_id, idempotency_key):
        if not idempotency_key:
            raise ValueError("staging bookmark requires an idempotency key")
        cached = self._cached(idempotency_key)
        if cached:
            return cached
        app = self.applications.get(application_id)
        if not app:
            raise KeyError(application_id)
        if app.status == "Bookmarked":
            self.idempotency[idempotency_key] = app
            return app
        if app.status not in {"Replay", "Relay"}:
            raise ValueError("only a direct child of Replay or Relay may be bookmarked")
        if app.status == "Replay":
            validate_transition(app.status, "Bookmarked")
        self.append_event(application_id, "replay_bookmarked", {
            "bullets": ["Position bookmarked from the operator Replay tab."],
            "source": "explicit replay workflow",
        }, idempotency_key + ":bookmark-event")
        app.status = "Bookmarked"
        app.updated_at = now()
        self.idempotency[idempotency_key] = app
        return app

    def repair_journal(self):
        return self.journal_status()

    def migrate_application_states(self):
        for application in self.applications.values():
            if application.status == 'Ready To Apply':
                application.status = 'Ready for Auto-Apply'
        return self.journal_status()
