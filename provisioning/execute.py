"""Run the lifecycle against the imported Vercel/Neon bootstrap and local hooks.

Host, database, and schema reuse the sibling auto-signup-vercel onboarding,
which owns the verified Vercel login and Neon bindings. Drive uses the local
user-OAuth provisioner. Application deployment publishes the Python service
onto that Vercel project. The workflow runner stays blocked.
"""
import json
import os
import subprocess
from pathlib import Path

from .bootstrap.handoffs import save
from .contracts import Outcome, TaskResult
from .chatgpt import prepare as prepare_chatgpt_connection
from .drive_folder import provision
from .state import ProvisioningFailure, State
from .vercel_deploy import deploy as deploy_application_release

REPO = Path(__file__).resolve().parents[1]
SIBLING = REPO.parent / 'auto-signup-vercel'
PROGRESS = REPO / '.onboarding' / 'up-stages.json'


def _result(outcome, evidence, resume=None):
    return TaskResult(outcome, None, tuple(evidence), resume)


def bootstrap_up():
    script = SIBLING / 'onboard.sh'
    if not script.is_file():
        return _result(Outcome.BLOCKED, ('auto-signup-vercel/onboard.sh is not available',),
                       'Place the sibling project next to this repository and rerun.')
    env = os.environ.copy()
    env.setdefault('ONBOARD_PYTHON', '/usr/bin/python3')
    completed = subprocess.run(
        [str(script), '--existing-account', '--auth', 'browser'],
        cwd=str(SIBLING), env=env, text=True)
    if completed.returncode != 0:
        return _result(Outcome.FAILED, ('sibling bootstrap exited ' + str(completed.returncode),),
                       'Rerun after checking the sibling .onboarding checkpoint. No rollback was applied.')
    return _result(Outcome.UNCHANGED, (
        'sibling bootstrap completed for the existing Vercel project, Neon database, roles, and schema',
    ))


def drive_up(path):
    if not path:
        return _result(Outcome.BLOCKED, ('No Drive folder path was given',),
                       'Set drive_path in deployment/defaults.json and rerun ./deployment/ctl.sh drive')
    try:
        result = provision(path)
    except ProvisioningFailure as exc:
        return _result(Outcome.FAILED, (str(exc),), 'Rerun ./deployment/ctl.sh drive after the reported condition is fixed.')
    changed = result['outcome'] == 'changed' or result['root_changed']
    return _result(Outcome.CHANGED if changed else Outcome.UNCHANGED, (
        'Root directory: ' + path,
        'folder ' + result['folder_id'],
        'project ' + result['project_id'],
        'service account ' + result['service_account'],
        'artifact root ' + result['artifact_root_url'],
    ))


def prepare_applicant_up():
    from services.onboarding.drive_applicant import prepare_applicant, storage_from_checkpoint
    runtime_path = REPO / 'config' / 'runtime.json'
    config = json.loads(runtime_path.read_text())
    drive = config['google_drive']
    profile_path = Path(drive['applicant_profile_path']).expanduser()
    if not profile_path.is_absolute():
        profile_path = REPO / profile_path
    local = json.loads(profile_path.read_text()) if profile_path.exists() else {}
    profile = prepare_applicant(storage_from_checkpoint(), profile_path,
        countries=(local.get('job_search') or {}).get('countries'),
        calendly_url=(config.get('scheduling') or {}).get('calendly_url'))
    drive['application_root_path'] = 'Applications'
    drive['applicant_directory'] = 'Applicant'
    from services.onboarding.main import save_runtime
    save_runtime(str(runtime_path), config)
    return _result(Outcome.CHANGED, ('Applicant résumé verified in Drive',
        'Extracted profile saved at Applicant/applicant_profile.json',))


def blocked(name, resume):
    return _result(Outcome.BLOCKED, (name + ' has no registered live adapter',), resume)


def up_steps(drive_path):
    # Database and schema are inside the sibling bootstrap, so they are not separate steps here.
    return (
        ('provision_application_host', bootstrap_up),
        ('provision_drive', lambda: drive_up(drive_path)),
        ('prepare_applicant', prepare_applicant_up),
        ('deploy_application', deploy_application_release),
        ('prepare_chatgpt_connection', prepare_chatgpt_connection),
        ('provision_runner_host', lambda: blocked(
            'provision_runner_host',
            'Add a Docker Compose runner service with a persistent volume before starting workflow workers.')),
    )


def load_progress(path=PROGRESS):
    path = Path(path)
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    completed = data.get('completed') if isinstance(data, dict) else None
    if not isinstance(completed, list) or not all(isinstance(item, str) for item in completed):
        raise ProvisioningFailure('The up progress file is unreadable: ' + str(path))
    return completed


def save_progress(completed, path=PROGRESS):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    save(path, {'version': 1, 'completed': list(completed)})
    path.chmod(0o600)


def resume_from(steps, completed):
    """Keep only a leading run of completed stages. The next stage is where a rerun continues."""
    names = [name for name, _action in steps]
    prefix = []
    for name in names:
        if name not in completed:
            break
        prefix.append(name)
    return prefix, [item for item in steps if item[0] not in prefix]


def run_up(drive_path=None, completed=()):
    """Execute implemented hooks in plan order. Stop after the first non-success."""
    state = State()
    steps = up_steps(drive_path)
    done, pending = resume_from(steps, completed)
    results = []
    with state.locked():
        for name, action in pending:
            try:
                result = action()
            except (ProvisioningFailure, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                result = _result(Outcome.FAILED, (name + ': ' + str(exc),), 'Fix the reported condition and rerun.')
            results.append((name, result))
            if result.outcome not in (Outcome.CHANGED, Outcome.UNCHANGED):
                save_progress(done)
                break
            done.append(name)
            save_progress(done)
    return results
