"""Durable bookkeeping for human approval requests.

Approval records are operational stage state, not application state. They
are kept separate from the Interviews Journal and never execute the approved
external action themselves.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
import uuid
from threading import RLock
from .synchronization import synchronized


DECISIONS = {"pending", "approved", "rejected"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ApprovalRequest:
    request_id: str
    action: str
    workload_id: str
    context: dict
    status: str = "pending"
    decision_note: str = ""
    created_at: str = field(default_factory=timestamp)
    updated_at: str = field(default_factory=timestamp)


class ApprovalRegistry:
    def __init__(self, storage_path=None):
        self._lock = RLock()
        self.storage_path = storage_path
        self.records = {}
        self.idempotency = {}
        self._load()

    @synchronized
    def _load(self):
        if not self.storage_path or not os.path.exists(self.storage_path):
            return
        with open(self.storage_path) as handle:
            raw = json.load(handle)
        for request_id, value in raw.get("records", {}).items():
            self.records[request_id] = ApprovalRequest(**value)
        self.idempotency.update(raw.get("idempotency", {}))

    @synchronized
    def _persist(self):
        if not self.storage_path:
            return
        parent = os.path.dirname(os.path.abspath(self.storage_path))
        os.makedirs(parent, exist_ok=True)
        temporary = self.storage_path + ".tmp"
        with open(temporary, "w") as handle:
            json.dump({
                "records": {key: asdict(value) for key, value in self.records.items()},
                "idempotency": self.idempotency,
            }, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, self.storage_path)

    @synchronized
    def request(self, action, workload_id, context=None, idempotency_key=None):
        if idempotency_key and idempotency_key in self.idempotency:
            return self.records[self.idempotency[idempotency_key]]
        record = ApprovalRequest("approval_" + uuid.uuid4().hex, action, workload_id, context or {})
        self.records[record.request_id] = record
        if idempotency_key:
            self.idempotency[idempotency_key] = record.request_id
        self._persist()
        return record

    @synchronized
    def decide(self, request_id, status, note=""):
        if status not in DECISIONS - {"pending"}:
            raise ValueError("approval decision must be approved or rejected")
        record = self.records.get(request_id)
        if not record:
            raise KeyError(request_id)
        if record.status != "pending" and record.status != status:
            raise ValueError("approval request is already " + record.status)
        record.status = status
        record.decision_note = note
        record.updated_at = timestamp()
        self._persist()
        return record

    @synchronized
    def pending(self):
        return [record for record in self.records.values() if record.status == "pending"]

    @synchronized
    def report(self):
        return {
            "counts": {
                status: sum(record.status == status for record in self.records.values())
                for status in sorted(DECISIONS)
            },
            "pending": [asdict(record) for record in self.pending()],
        }


def approval_registry_from_environment():
    return ApprovalRegistry(os.environ.get("APPROVAL_REGISTRY_PATH") or None)
