"""The public Python entrypoint owns orchestration; no agent-side setup is needed."""
import importlib
import io
import json

import pytest

from provisioning import execute
from provisioning.__main__ import main
from provisioning.contracts import Outcome, TaskResult
from provisioning.drive_login import GoogleConsentRequired

ORIGINAL_DRIVE_UP = execute.drive_up


def runtime(repo):
    directory = repo / 'config'
    directory.mkdir(exist_ok=True)
    profile = directory / 'applicant_profile.json'
    profile.write_text('{}')
    (directory / 'runtime.json').write_text(json.dumps({'google_drive': {
        'connection': 'test', 'interview_journal_url': 'https://docs.google.com/document/d/journal/edit',
        'artifact_root_url': 'https://drive.google.com/drive/folders/root',
        'applicant_profile_path': str(profile),
    }}))


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setattr(execute, 'REPO', tmp_path)
    monkeypatch.setattr(execute, 'ONBOARDING', tmp_path / '.onboarding')
    monkeypatch.setattr(execute, 'PROGRESS', tmp_path / '.onboarding/up-stages.json')
    monkeypatch.setattr(execute.sys, 'stdin', io.StringIO())
    calls = []
    for name in ('bootstrap_up', 'drive_up', 'prepare_applicant_up',
                 'deploy_application_release', 'prepare_chatgpt_connection', 'deploy_runner'):
        def step(*args, _name=name):
            calls.append(_name)
            return TaskResult(Outcome.UNCHANGED, None, (_name + ' verified',))
        monkeypatch.setattr(execute, name, step)
    return tmp_path, calls


def test_public_up_reconciles_every_stage_even_with_complete_old_checkpoint(installation):
    repo, calls = installation
    runtime(repo)
    execute.save_progress([name for name, _ in execute.up_steps('root')], execute.PROGRESS)
    assert main(['up']) == 0
    first = list(calls)
    assert first == ['bootstrap_up', 'drive_up', 'prepare_applicant_up',
                     'deploy_application_release', 'prepare_chatgpt_connection', 'deploy_runner']
    assert main(['up']) == 0
    assert calls == first + first


def test_missing_noninteractive_configuration_blocks_before_provider_calls(installation, capsys):
    repo, calls = installation
    assert main(['up']) == 2
    assert calls == []
    assert './deployment/ctl.sh onboard' in capsys.readouterr().out
    assert json.loads(execute.PROGRESS.read_text())['completed'] == []
    from pypdf import PdfReader
    text = ' '.join(PdfReader(repo / '.onboarding/provisioning-report.pdf').pages[0].extract_text().split())
    assert 'configure_runtime: blocked' in text
    assert './deployment/ctl.sh onboard' in text


def test_interactive_first_run_is_owned_by_up_not_an_agent_side_command(installation, monkeypatch):
    repo, calls = installation
    class Terminal(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr(execute.sys, 'stdin', Terminal())
    onboard = importlib.import_module('services.onboarding.main')
    def wizard(argv):
        calls.append('onboard')
        runtime(repo)
        return 0
    monkeypatch.setattr(onboard, 'main', wizard)
    assert main(['up']) == 0
    assert calls[0:2] == ['onboard', 'bootstrap_up']


def test_oauth_gate_prevents_dependent_stages_and_is_reported_by_script(installation, monkeypatch, capsys):
    repo, calls = installation
    runtime(repo)
    monkeypatch.setattr(execute, 'drive_up', ORIGINAL_DRIVE_UP)
    def needs_consent(path):
        calls.append('drive_up')
        raise GoogleConsentRequired('Create the dedicated Desktop OAuth client')
    monkeypatch.setattr(execute, 'provision', needs_consent)
    assert main(['up']) == 2
    assert calls == ['bootstrap_up', 'drive_up']
    assert 'provision_drive: blocked' in capsys.readouterr().out
    assert json.loads(execute.PROGRESS.read_text())['completed'] == ['configure_runtime', 'provision_application_host']


def test_missing_applicant_cache_has_a_script_owned_recovery_path(installation, capsys):
    repo, calls = installation
    runtime(repo)
    (repo / 'config/applicant_profile.json').unlink()
    assert main(['up']) == 2
    assert calls == []
    assert './deployment/ctl.sh onboard --applicant' in capsys.readouterr().out


def test_explicit_onboarding_shares_the_installation_lock(installation, monkeypatch):
    repo, calls = installation
    onboard = importlib.import_module('services.onboarding.main')
    from provisioning.state import State, ProvisioningFailure
    def wizard(argv):
        assert argv == ['--applicant']
        with pytest.raises(ProvisioningFailure, match='lock'):
            with State(repo / '.onboarding').locked():
                raise AssertionError('onboarding did not hold its installation lock')
        return 0
    monkeypatch.setattr(onboard, 'main', wizard)
    assert main(['onboard', '--applicant']) == 0
    assert calls == []


def test_unexpected_stage_error_is_reported_and_stops_the_script(installation, monkeypatch, capsys):
    repo, calls = installation
    runtime(repo)
    def unexpected():
        calls.append('bootstrap_up')
        raise TypeError('private diagnostic must not be printed')
    monkeypatch.setattr(execute, 'bootstrap_up', unexpected)
    assert main(['up']) == 2
    assert calls == ['bootstrap_up']
    output = capsys.readouterr().out
    assert 'unexpected TypeError' in output
    assert 'private diagnostic' not in output
    assert (repo / '.onboarding/provisioning-report.pdf').is_file()


def test_overlapping_up_fails_cleanly_before_any_provider_calls(installation, capsys):
    repo, calls = installation
    runtime(repo)
    from provisioning.state import State
    with State(repo / '.onboarding').locked():
        assert main(['up']) == 2
    assert calls == []
    assert 'Another provisioning process' in capsys.readouterr().err
