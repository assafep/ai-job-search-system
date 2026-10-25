"""Persistent operational registries owned by the hosted Application Service.

Construct only while holding the installation workflow-execution lock. Files in
/tmp and process-local dictionaries are never the authoritative hosted registry.
"""
from dataclasses import asdict
from .database import connect
from .initialization import resource, save_resource
from services.workload_registry import WorkloadRegistry, WorkloadRecord
from services.approval_registry import ApprovalRegistry, ApprovalRequest


class HostedWorkloads(WorkloadRegistry):
    def _load(self):
        with connect() as connection:
            records = resource(connection, 'hosted_workloads') or {}
        self.records = {key: WorkloadRecord(**value) for key, value in records.items()}

    def _persist(self):
        with connect() as connection:
            save_resource(connection, 'hosted_workloads', {key: asdict(value) for key, value in self.records.items()})


class HostedApprovals(ApprovalRegistry):
    def _load(self):
        with connect() as connection:
            data = resource(connection, 'hosted_approvals') or {}
        self.records = {key: ApprovalRequest(**value) for key, value in data.get('records', {}).items()}
        self.idempotency = data.get('idempotency', {})

    def _persist(self):
        with connect() as connection:
            save_resource(connection, 'hosted_approvals', {
                'records': {key: asdict(value) for key, value in self.records.items()},
                'idempotency': self.idempotency})
