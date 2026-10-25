import json

from services.onboarding import main as onboarding


def test_onboarding_resource_url_validators():
    assert onboarding.google_doc_url("https://docs.google.com/document/d/journal/edit")
    assert onboarding.google_doc_url("https://docs.google.com/document/u/0/d/journal/edit")
    assert onboarding.drive_folder_url("https://drive.google.com/drive/folders/root?usp=sharing")
    assert onboarding.drive_folder_url("https://drive.google.com/drive/u/0/folders/root?usp=sharing")
    assert onboarding.application_root_path("applications/active")
    assert onboarding.application_root_path("")
    assert not onboarding.application_root_path("../outside")
    assert not onboarding.application_root_path(".")
    assert not onboarding.google_doc_url("https://example.test/document/d/journal")
    assert not onboarding.drive_folder_url("https://drive.google.com/file/d/file")
    assert onboarding.web_url("https://calendly.com/example/intro")
    assert not onboarding.web_url("not-a-url")
    assert not onboarding.web_url("https://")
    assert onboarding.country_codes("IL, US")
    assert not onboarding.country_codes("Israel, U1")


def test_first_run_onboarding_writes_runtime_and_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from services.onboarding import drive_applicant
    runtime_path = tmp_path / "runtime.json"
    profile_path = tmp_path / "applicant_profile.json"
    monkeypatch.setattr(onboarding, "CONFIG_PATH", str(runtime_path))
    monkeypatch.setattr(onboarding, "PROFILE_PATH", str(profile_path))
    calls = []
    storage = SimpleNamespace(owner=SimpleNamespace(root_folder_id='root'))
    monkeypatch.setattr(drive_applicant, 'connect', lambda folder: calls.append(('connect', folder)) or storage)
    def prepare(actual, path, countries, calendly):
        assert actual is storage
        assert not runtime_path.exists()
        calls.append(('prepare', countries))
        profile = {'schema_version': 1, 'personal': {'phone': '+972502345678'},
                   'professional': {'headline': 'Engineer'}, 'employment': [],
                   'source_resume': {'path': 'Applicant/resume.pdf'}}
        Path(path).write_text(json.dumps(profile))
        return profile
    from pathlib import Path
    monkeypatch.setattr(drive_applicant, 'prepare_applicant', prepare)
    monkeypatch.setattr(onboarding, 'obtain_email_token', lambda email: True)
    answers = iter(['default', 'https://docs.google.com/document/d/journal', 'IL,US',
                    'https://calendly.com/example/intro', 'https://drive.google.com/drive/folders/root'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    assert onboarding.main() == 0
    config = json.loads(runtime_path.read_text())
    assert config['google_drive']['application_root_path'] == 'Applications'
    assert config['google_drive']['applicant_directory'] == 'Applicant'
    assert config['google_drive']['applicant_profile_path'] == str(profile_path)
    assert calls == [('connect', 'https://drive.google.com/drive/folders/root'), ('prepare', ['IL', 'US'])]
    assert json.loads(profile_path.read_text())['source_resume']['path'] == 'Applicant/resume.pdf'


def test_completed_onboarding_backfills_bookmark_settings(tmp_path, monkeypatch):
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps({
        "google_drive": {"connection": "default"},
        "scheduling": {"calendly_url": "https://calendly.com/example/intro"},
        "scout": {"coding_agent": "codex"},
    }))
    monkeypatch.setattr(onboarding, "CONFIG_PATH", str(runtime_path))
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(AssertionError(prompt)))

    assert onboarding.main() == 0
    config = json.loads(runtime_path.read_text())
    assert config["scout"] == {"coding_agent": "codex"}
    assert config["bookmark-discovered"] == {
        "coding_agent": "grok", "model": "grok-4.7", "effort": "medium"}


def test_runtime_profile_path_is_relative_for_project_files(monkeypatch):
    project_root = onboarding.os.path.abspath(
        onboarding.os.path.join(onboarding.os.path.dirname(onboarding.__file__), "..", "..")
    )
    profile_path = onboarding.os.path.join(project_root, "config", "applicant_profile.json")
    assert onboarding.runtime_profile_path(profile_path) == "config/applicant_profile.json"


def test_onboarding_does_not_leave_runtime_after_drive_write_failure(tmp_path, monkeypatch):
    from services.onboarding import drive_applicant
    runtime_path = tmp_path / 'runtime.json'
    monkeypatch.setattr(onboarding, 'CONFIG_PATH', str(runtime_path))
    monkeypatch.setattr(drive_applicant, 'connect', lambda folder: object())
    def failed(*args):
        raise ValueError('Drive profile verification failed')
    monkeypatch.setattr(drive_applicant, 'prepare_applicant', failed)
    answers = iter(['default', 'https://docs.google.com/document/d/journal', 'IL',
                    'https://calendly.com/example/intro', 'https://drive.google.com/drive/folders/root'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    assert onboarding.main() == 1
    assert not runtime_path.exists()
