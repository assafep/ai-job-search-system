"""Run the lifecycle using repository-owned provider adapters.

Host, database, and schema use Docker-based provisioning.
Drive uses the local user-OAuth provisioner. Application deployment publishes
the Python service. The Docker workload runner is deployed without activation.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from .bootstrap.handoffs import save
from .contracts import Outcome, TaskResult
from .chatgpt import prepare as prepare_chatgpt_connection
from .drive_folder import provision
from .drive_login import GoogleConsentRequired
from .state import ProvisioningFailure, State
from .docker_runner import deploy as deploy_runner

REPO = Path(__file__).resolve().parents[1]
ONBOARDING = REPO / '.onboarding'
PROGRESS = ONBOARDING / 'up-stages.json'


def _result(outcome, evidence, resume=None):
    return TaskResult(outcome, None, tuple(evidence), resume)


def onboard_runtime(argv=None):
    """The explicit onboarding command shares the installation mutation lock."""
    from services.onboarding.main import main as onboard
    with State(ONBOARDING).locked():
        return onboard(argv or [])


def configure_runtime_up():
    """Own first-run onboarding; never depend on an operator's Python snippet."""
    runtime = REPO / 'config' / 'runtime.json'
    if not runtime.is_file():
        if not sys.stdin.isatty():
            return _result(Outcome.BLOCKED, ('config/runtime.json is missing',),
                           'Run ./deployment/ctl.sh onboard in an interactive terminal, then rerun ./deployment/ctl.sh up.')
        from services.onboarding.main import main as onboard
        status = onboard([])
        if status != 0:
            return _result(Outcome.BLOCKED, ('First-run onboarding did not complete',),
                           'Complete ./deployment/ctl.sh onboard, then rerun ./deployment/ctl.sh up.')
    config = json.loads(runtime.read_text())
    if not isinstance(config, dict):
        raise ValueError('runtime configuration must be a JSON object')
    drive = config.get('google_drive')
    profile = drive.get('applicant_profile_path') if isinstance(drive, dict) else None
    if isinstance(profile, str) and profile:
        cache = Path(profile).expanduser()
        if not cache.is_absolute():
            cache = REPO / cache
        if not cache.is_file():
            if not sys.stdin.isatty():
                return _result(Outcome.BLOCKED, ('The configured applicant cache is missing',),
                               'Run ./deployment/ctl.sh onboard --applicant, then rerun ./deployment/ctl.sh up.')
            from services.onboarding.main import main as onboard
            if onboard(['--applicant']) != 0:
                return _result(Outcome.BLOCKED, ('Applicant onboarding did not complete',),
                               'Complete ./deployment/ctl.sh onboard --applicant, then rerun up.')
    from services.runtime_config import load
    load(str(runtime), require=True)
    return _result(Outcome.UNCHANGED, ('Runtime configuration and applicant cache validated',))


def bootstrap_up():
    """Placeholder: Docker-based host provisioning is not yet implemented."""
    return _result(Outcome.BLOCKED, ('Docker-based host provisioning is not yet implemented',),
                   'Configure Docker-based deployment manually, then rerun ./deployment/ctl.sh up.')


def drive_up(path):
    if not path:
        return _result(Outcome.BLOCKED, ('No Drive folder path was given',),
                       'Set drive_path in deployment/defaults.json and rerun ./deployment/ctl.sh drive')
    try:
        result = provision(path)
    except GoogleConsentRequired as exc:
        return _result(Outcome.BLOCKED, (str(exc),), 'Configure the dedicated OAuth client, then rerun ./deployment/ctl.sh up.')
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
    # Host provisioning also verifies the database and schema.
    return (
        ('configure_runtime', configure_runtime_up),
        ('provision_application_host', bootstrap_up),
        ('provision_drive', lambda: drive_up(drive_path)),
        ('prepare_applicant', prepare_applicant_up),
        ('prepare_chatgpt_connection', prepare_chatgpt_connection),
        ('provision_runner_host', deploy_runner),
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


def run_up(drive_path=None):
    """Reconcile every stage on each invocation; checkpoints are diagnostic only."""
    state = State(ONBOARDING)
    done = []
    pending = up_steps(drive_path)
    results = []
    with state.locked():
        for name, action in pending:
            print(f'Starting step: {name}', flush=True)
            try:
                result = action()
            except (ProvisioningFailure, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                exc_args = ' '.join(str(a) for a in exc.args) if exc.args else ''
                detail = f'{type(exc).__name__}'
                if exc_args:
                    detail += ': ' + exc_args
                if hasattr(exc, 'filename') and exc.filename:
                    detail += ' (file: ' + exc.filename + ')'
                result = _result(Outcome.FAILED, (name + ': ' + detail,), 'Fix the reported condition and rerun.')
            except Exception as exc:
                result = _result(Outcome.FAILED,
                                 (name + ': unexpected ' + type(exc).__name__,),
                                 'Check this stage configuration and rerun. Dependent stages were not executed.')
            results.append((name, result))
            if result.outcome not in (Outcome.CHANGED, Outcome.UNCHANGED):
                save_progress(done, PROGRESS)
                break
            done.append(name)
            save_progress(done, PROGRESS)
    return results
