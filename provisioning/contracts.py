"""Lifecycle hook interfaces; provider commands belong in future adapters.

See deployment/lifecycle.md for recovery, authorization and idempotency rules.
SecretRef contains a location/version, never a credential value. Infrastructure
checkpoints are separate from application state, which remains API-owned.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Generic, Mapping, Optional, Protocol, Tuple, TypeVar


class Outcome(str, Enum):
    CHANGED = 'changed'
    UNCHANGED = 'unchanged'
    BLOCKED = 'blocked'
    UNSUPPORTED = 'unsupported'
    FAILED = 'failed'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class SecretRef:
    location: str
    version: str


class SecretResolver(Protocol):
    def resolve(self, reference: SecretRef) -> str:
        """Resolve only inside an adapter; never return the value in a result."""
        ...


class CheckpointStore(Protocol):
    def load(self, operation_key: str) -> Optional[Mapping[str, object]]:
        ...

    def save(self, operation_key: str, checkpoint: Mapping[str, object]) -> None:
        """Atomically persist intent, remote IDs and secret references under lock."""
        ...


@dataclass(frozen=True)
class TaskContext:
    installation_id: str
    environment: str
    operation_key: str
    desired_digest: str
    # Acquired by the orchestrator before mutation; covers the whole installation.
    lease_id: str
    checkpoints: CheckpointStore
    secrets: SecretResolver


T = TypeVar('T')


@dataclass(frozen=True)
class TaskResult(Generic[T]):
    outcome: Outcome
    value: Optional[T]
    evidence: Tuple[str, ...]
    resume_instruction: Optional[str] = None


@dataclass(frozen=True)
class Setup:
    name: str
    application_provider: str
    database_provider: str
    runner_provider: str
    drive_mode: str


@dataclass(frozen=True)
class CapabilityCheck:
    task: str
    supported: bool
    reason: str


@dataclass(frozen=True)
class HostSpec:
    provider: str
    account: str
    name: str
    region: str
    execution_model: str  # request (application API) | process (API or runner)
    required_capabilities: Tuple[str, ...] = ()


@dataclass(frozen=True)
class HostTarget:
    provider: str
    account: str
    resource_id: str
    name: str
    activation_model: str  # process-start-stop | production-pause-resume


@dataclass(frozen=True)
class DatabaseSpec:
    provider: str
    name: str
    engine: str
    engine_version: str
    application_role: str


@dataclass(frozen=True)
class ApplicationDatabaseAccess:
    resource_id: str
    database: str
    engine: str
    role: str
    connection: SecretRef


@dataclass(frozen=True)
class DatabaseAccess:
    application: ApplicationDatabaseAccess
    migrator_role: str
    migrator_connection: SecretRef


@dataclass(frozen=True)
class SchemaSpec:
    artifact_path: str
    engine: str
    version: str
    sha256: str


@dataclass(frozen=True)
class SchemaState:
    database_resource_id: str
    version: str
    sha256: str
    grants_verified: bool


@dataclass(frozen=True)
class DriveSpec:
    mode: str  # user-oauth | workspace-service-account
    folder_name: str
    folder_key: str  # installation marker; name alone is not an identity
    bootstrap_credential: SecretRef
    parent_id: Optional[str] = None
    shared_drive_id: Optional[str] = None
    google_cloud_project: Optional[str] = None


@dataclass(frozen=True)
class DriveAccess:
    root_folder_id: str
    principal: str
    credential: SecretRef
    authorization_model: str  # app-created-selected-files | folder-acl
    shared_drive_id: Optional[str] = None


@dataclass(frozen=True)
class ReleaseSpec:
    artifact_uri: str
    artifact_sha256: str
    entrypoint: str
    properties: Mapping[str, str]
    secret_bindings: Mapping[str, SecretRef]


@dataclass(frozen=True)
class ApplicationRelease:
    release: ReleaseSpec
    database: ApplicationDatabaseAccess
    schema: SchemaState
    drive: DriveAccess
    api_token: SecretRef


@dataclass(frozen=True)
class RunnerRelease:
    release: ReleaseSpec
    application_url: str
    application_token: SecretRef
    durable_state_location: str
    enabled_stages: Tuple[str, ...]


@dataclass(frozen=True)
class Deployment:
    host: HostTarget
    release_id: str
    fingerprint: str
    candidate_url: Optional[str]
    verification_evidence: Tuple[str, ...]


@dataclass(frozen=True)
class ServiceState:
    host: HostTarget
    state: str  # active | stopped; draining is not stopped success
    endpoint: Optional[str]
    in_flight: Optional[int]


class LifecycleHooks(Protocol):
    """A composed provider bundle implements these hooks, not the common CLI.

    Call preflight for the entire plan before any mutation. Every hook observes
    remote state before changing it; failed/unknown outcomes block dependants.
    A successful no-op returns UNCHANGED with verified evidence and an output.
    Deploy stages a release without enabling runner consumption. Start/stop
    operate on existing bindings and never provision resources implicitly.
    """

    def preflight(self, setup: Setup, tasks: Tuple[str, ...]) -> Tuple[CapabilityCheck, ...]:
        ...

    def provision_application_host(self, ctx: TaskContext, spec: HostSpec) -> TaskResult[HostTarget]:
        ...

    def provision_database(self, ctx: TaskContext, spec: DatabaseSpec,
                           host: Optional[HostTarget]) -> TaskResult[DatabaseAccess]:
        ...

    def apply_database_schema(self, ctx: TaskContext, database: DatabaseAccess,
                              schema: SchemaSpec) -> TaskResult[SchemaState]:
        ...

    def provision_drive(self, ctx: TaskContext, spec: DriveSpec) -> TaskResult[DriveAccess]:
        ...

    def deploy_application(self, ctx: TaskContext, host: HostTarget,
                           release: ApplicationRelease) -> TaskResult[Deployment]:
        ...

    def start_application(self, ctx: TaskContext, deployment: Deployment) -> TaskResult[ServiceState]:
        ...

    def stop_application(self, ctx: TaskContext, host: HostTarget) -> TaskResult[ServiceState]:
        ...

    def provision_runner_host(self, ctx: TaskContext, spec: HostSpec) -> TaskResult[HostTarget]:
        """Ensure a process/container host; require execution_model='process'.

        Runner hosting is independent of the application API provider. Stage
        instances call that API over HTTP.
        """
        ...

    def deploy_runner(self, ctx: TaskContext, host: HostTarget,
                      release: RunnerRelease) -> TaskResult[Deployment]:
        ...

    def start_runner(self, ctx: TaskContext, deployment: Deployment,
                     application: ServiceState) -> TaskResult[ServiceState]:
        ...

    def stop_runner(self, ctx: TaskContext, host: HostTarget,
                    drain_timeout_seconds: int) -> TaskResult[ServiceState]:
        ...
