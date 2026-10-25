"""Operational workload lifecycle for scheduling and reconciliation.

This registry tracks execution bookkeeping, not application state. Application
mutations continue to go through the Application Service and the Interviews Journal.
"""

from dataclasses import dataclass, field
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from threading import RLock
from .synchronization import synchronized


STATUSES = {"queued", "running", "succeeded", "failed", "dead_letter", "awaiting_input"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkloadRecord:
    workload_id: str
    workflow: str
    event: dict
    status: str = "queued"
    attempts: int = 0
    error: str = None
    created_at: str = field(default_factory=timestamp)
    updated_at: str = field(default_factory=timestamp)
    result: dict = None


class WorkloadRegistry:
    """Reference registry with idempotent enqueue and bounded retries."""

    def __init__(self, max_attempts=3, storage_path=None):
        self._lock = RLock()
        self.max_attempts = max_attempts
        self.storage_path = storage_path
        self.records = {}
        self._load()

    @synchronized
    def _load(self):
        if not self.storage_path or not os.path.exists(self.storage_path):
            return
        with open(self.storage_path) as handle:
            raw_records = json.load(handle)
        for workload_id, raw in raw_records.items():
            self.records[workload_id] = WorkloadRecord(**raw)

    @synchronized
    def _persist(self):
        if not self.storage_path:
            return
        parent = os.path.dirname(os.path.abspath(self.storage_path))
        if not os.path.exists(parent):
            os.makedirs(parent)
        payload = {
            workload_id: {
                "workload_id": record.workload_id,
                "workflow": record.workflow,
                "event": record.event,
                "status": record.status,
                "attempts": record.attempts,
                "error": record.error,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "result": record.result,
            }
            for workload_id, record in self.records.items()
        }
        temporary_path = self.storage_path + ".tmp"
        with open(temporary_path, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_path, self.storage_path)

    @synchronized
    def enqueue(self, envelope):
        workload_id = envelope["workload_id"]
        existing = self.records.get(workload_id)
        if existing:
            return existing
        record = WorkloadRecord(workload_id, envelope["workflow"], envelope["event"])
        self.records[workload_id] = record
        self._persist()
        return record

    @synchronized
    def claim(self, workload_id):
        record = self._get(workload_id)
        if record.status not in {"queued", "failed", "awaiting_input"}:
            raise ValueError("workload is not claimable: " + record.status)
        if record.attempts >= self.max_attempts:
            record.status = "dead_letter"
            record.updated_at = timestamp()
            self._persist()
            raise ValueError("workload exceeded retry limit")
        record.attempts += 1
        record.status = "running"
        record.error = None
        record.updated_at = timestamp()
        self._persist()
        return record

    @synchronized
    def complete(self, workload_id):
        record = self._get(workload_id)
        if record.status != "running":
            raise ValueError("only running workloads can complete")
        record.status = "succeeded"
        record.updated_at = timestamp()
        self._persist()
        return record

    @synchronized
    def defer(self, workload_id, error):
        """Return a claimed workload to the queue without consuming a retry.

        Used when the provider is temporarily unavailable. The claim already
        incremented the attempt counter, and a quota outage must not move the
        workload toward the dead-letter limit.
        """
        record = self._get(workload_id)
        if record.status != "running":
            raise ValueError("only running workloads can be deferred")
        record.attempts = max(0, record.attempts - 1)
        record.status = "queued"
        record.error = str(error)
        record.updated_at = timestamp()
        self._persist()
        return record

    @synchronized
    def fail(self, workload_id, error):
        record = self._get(workload_id)
        if record.status != "running":
            raise ValueError("only running workloads can fail")
        record.error = str(error)
        record.status = "dead_letter" if record.attempts >= self.max_attempts else "failed"
        record.updated_at = timestamp()
        self._persist()
        return record

    @synchronized
    def wait_for_input(self, workload_id, reason):
        record = self._get(workload_id)
        if record.status != 'running':
            raise ValueError('only running workloads can await input')
        record.status = 'awaiting_input'
        record.error = str(reason)
        record.attempts = max(0, record.attempts - 1)
        record.updated_at = timestamp()
        self._persist()
        return record

    @synchronized
    def reopen(self, workload_id):
        """Queue a dead-lettered workload again.

        The journal still owns whether the posting needs work. A dead letter
        only means the last attempts failed, so a later pass may claim it.
        """
        record = self._get(workload_id)
        if record.status != "dead_letter":
            return record
        record.status = "queued"
        record.attempts = 0
        record.updated_at = timestamp()
        self._persist()
        return record

    @synchronized
    def recover_incomplete(self):
        recovered = []
        for record in self.records.values():
            if record.status == "running":
                record.status = "queued" if record.attempts < self.max_attempts else "dead_letter"
                record.updated_at = timestamp()
                recovered.append(record)
        if recovered:
            self._persist()
        return recovered

    @synchronized
    def pending(self):
        return [record for record in self.records.values() if record.status in {"queued", "failed"}]

    @synchronized
    def report(self):
        """Return a JSON-safe operational snapshot for reconciliation/reporting."""
        counts = {status: 0 for status in sorted(STATUSES)}
        records = []
        history = []
        for record in self.records.values():
            counts[record.status] += 1
            item = {
                "workload_id": record.workload_id,
                "workflow": record.workflow,
                "status": record.status,
                "attempts": record.attempts,
                "error": record.error,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            }
            history.append(item)
            if record.status in {"queued", "running", "failed", "dead_letter", "awaiting_input"}:
                records.append(item)
        history.sort(key=lambda item: item["updated_at"], reverse=True)
        return {"counts": counts, "attention": records, "history": history}

    @synchronized
    def get(self, workload_id):
        """Return a workload record without changing its lifecycle state."""
        return self._get(workload_id)

    @synchronized
    def snapshot(self):
        return deepcopy(list(self.records.values()))

    @synchronized
    def checkpoint_result(self, workload_id, result):
        record = self._get(workload_id)
        record.result = deepcopy(result)
        record.updated_at = timestamp()
        self._persist()

    def _get(self, workload_id):
        if workload_id not in self.records:
            raise KeyError(workload_id)
        return self.records[workload_id]


def registry_from_environment():
    """Build the operational registry from deployment configuration."""
    raw_attempts = os.environ.get("WORKLOAD_MAX_ATTEMPTS", "3")
    try:
        max_attempts = int(raw_attempts)
    except ValueError:
        raise ValueError("WORKLOAD_MAX_ATTEMPTS must be an integer")
    if max_attempts < 1:
        raise ValueError("WORKLOAD_MAX_ATTEMPTS must be positive")
    return WorkloadRegistry(
        max_attempts=max_attempts,
        storage_path=os.environ.get("WORKLOAD_REGISTRY_PATH") or None,
    )
