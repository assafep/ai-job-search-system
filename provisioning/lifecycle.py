"""Declarative task skeleton. Planning never imports or invokes live providers."""

from dataclasses import dataclass, replace
from typing import Tuple

from .contracts import Setup


PROFILES = {
    'local-docker': Setup('local-docker', 'docker', 'docker-postgres', 'docker', 'user-oauth'),
}
DRIVE_MODES = ('user-oauth', 'workspace-service-account')
ACTIONS = ('up', 'start', 'stop')


@dataclass(frozen=True)
class TaskSpec:
    name: str
    provider: str
    dependencies: Tuple[str, ...]
    inputs: Tuple[str, ...]
    output: str


def select_setup(name, drive_mode='user-oauth'):
    if name not in PROFILES:
        raise ValueError('Unknown setup: ' + name)
    if drive_mode not in DRIVE_MODES:
        raise ValueError('Unknown Drive mode: ' + drive_mode)
    return replace(PROFILES[name], drive_mode=drive_mode)


def plan(setup, action='up'):
    """Return ordered hook specifications, including provider-specific dependencies.

    `up` describes an initial installation. Live update/drain orchestration must
    be implemented before applying it to a running installation. Start and stop
    require saved bindings and never include provisioning/deployment tasks.
    """
    if action not in ACTIONS:
        raise ValueError('Unknown lifecycle action: ' + action)
    if action == 'stop':
        return (
            TaskSpec('stop_runner', setup.runner_provider, (),
                     ('saved runner HostTarget', 'drain_timeout_seconds'), 'ServiceState'),
            TaskSpec('stop_application', setup.application_provider, ('stop_runner',),
                     ('saved application HostTarget',), 'ServiceState'),
        )
    application_start = TaskSpec(
        'start_application', setup.application_provider,
        ('deploy_application',) if action == 'up' else (),
        ('application Deployment',), 'ServiceState')
    runner_start = TaskSpec(
        'start_runner', setup.runner_provider,
        ('deploy_runner', 'start_application') if action == 'up' else ('start_application',),
        ('runner Deployment', 'application ServiceState'), 'ServiceState')
    if action == 'start':
        return (application_start, runner_start)
    return (
        TaskSpec('configure_runtime', 'local', (), ('runtime configuration or first-run onboarding inputs',),
                 'Validated runtime configuration'),
        TaskSpec('provision_application_host', setup.application_provider, ('configure_runtime',),
                 ('HostSpec',), 'HostTarget'),
        TaskSpec('provision_database', setup.database_provider,
                 (),
                 ('DatabaseSpec', 'optional application HostTarget'), 'DatabaseAccess'),
        TaskSpec('apply_database_schema', 'postgresql', ('provision_database',),
                 ('DatabaseAccess', 'SchemaSpec'), 'SchemaState'),
        TaskSpec('provision_drive', setup.drive_mode, (), ('DriveSpec',), 'DriveAccess'),
        TaskSpec('prepare_applicant', setup.drive_mode, ('provision_drive',),
                 ('DriveAccess', 'resume uploaded to Applicant'), 'Validated applicant profile'),
        TaskSpec('deploy_application', setup.application_provider,
                 ('provision_application_host', 'apply_database_schema', 'prepare_applicant'),
                 ('application HostTarget', 'ApplicationRelease'), 'Deployment'),
        TaskSpec('prepare_chatgpt_connection', 'chatgpt', ('deploy_application',),
                 ('verified application URL', 'service token', 'openapi.yaml'), 'Verified MCP connection handoff'),
        application_start,
        TaskSpec('provision_runner_host', setup.runner_provider, (), ('HostSpec',), 'HostTarget'),
        TaskSpec('deploy_runner', setup.runner_provider,
                 ('provision_runner_host', 'start_application'),
                 ('runner HostTarget', 'RunnerRelease'), 'Deployment'),
        runner_start,
    )


def implementation_gaps(setup):
    """Inspection findings, not a live provider capability probe."""
    gaps = [
        'Live up runs Docker-based provisioning, then Drive folder binding, applicant preparation, application deployment, and MCP connection verification. Docker runner images and stopped containers are deployed with persistent storage.',
        'Database/Drive readiness and deployment reuse are verified after deployment.',
        'Drive folder provisioning is implemented for user OAuth; it blocks when refresh credentials are absent.',
    ]
    if setup.drive_mode == 'workspace-service-account':
        gaps.append('Workspace mode needs Shared Drive access, Cloud IAM authorization and service-account support in the Drive adapter.')
    else:
        gaps.append('My Drive needs configured OAuth consent/client and browser authorization; drive.file is not a folder-only credential.')
    gaps.append('Runner deployment does not activate workloads; website interactions remain on the native ChatGPT host.')
    if setup.database_provider == 'docker-postgres':
        gaps.append('Docker PostgreSQL provisioning is not implemented; existing Compose only defines the API.')
    return tuple(gaps)
