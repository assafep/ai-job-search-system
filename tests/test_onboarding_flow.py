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
    runtime_path = tmp_path / "runtime.json"
    profile_path = tmp_path / "applicant_profile.json"
    resume_path = tmp_path / "resume.pdf"
    resume_path.write_bytes(b"resume")
    monkeypatch.setattr(onboarding, "CONFIG_PATH", str(runtime_path))
    monkeypatch.setattr(onboarding, "PROFILE_PATH", str(profile_path))
    monkeypatch.delenv("GOOGLE_ACCESS_TOKEN", raising=False)
    answers = iter([
        "default",
        "https://docs.google.com/document/d/journal",
        "https://drive.google.com/drive/folders/root",
        "applications",
        "IL,US",
        "https://calendly.com/example/intro",
        str(resume_path),
        "+972502345678",
    ])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert onboarding.main() == 0
    config = json.loads(runtime_path.read_text())
    assert config["google_drive"]["interview_journal_url"].endswith("journal")
    assert config["google_drive"]["applicant_profile_path"] == str(profile_path)
    assert config["google_drive"]["application_root_path"] == "applications"
    assert config["scheduling"]["calendly_url"] == "https://calendly.com/example/intro"
    saved_profile = json.loads(profile_path.read_text())
    assert saved_profile["source_resume"]["file_name"] == "resume.pdf"
    assert saved_profile['personal']['phone'] == '+972502345678'
    assert saved_profile["application_defaults"]["calendly_url"] == (
        "https://calendly.com/example/intro"
    )
    assert saved_profile["job_search"]["countries"] == ["IL", "US"]
    assert saved_profile["job_search"]["search_terms"] == []


def test_runtime_profile_path_is_relative_for_project_files(monkeypatch):
    project_root = onboarding.os.path.abspath(
        onboarding.os.path.join(onboarding.os.path.dirname(onboarding.__file__), "..", "..")
    )
    profile_path = onboarding.os.path.join(project_root, "config", "applicant_profile.json")
    assert onboarding.runtime_profile_path(profile_path) == "config/applicant_profile.json"


def test_onboarding_does_not_leave_runtime_after_drive_validation_failure(tmp_path, monkeypatch):
    runtime_path = tmp_path / "runtime.json"
    profile_path = tmp_path / "applicant_profile.json"
    resume_path = tmp_path / "resume.pdf"
    resume_path.write_bytes(b"resume")
    monkeypatch.setattr(onboarding, "CONFIG_PATH", str(runtime_path))
    monkeypatch.setattr(onboarding, "PROFILE_PATH", str(profile_path))
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "token")
    monkeypatch.setattr(
        onboarding,
        "sync_profile_to_drive",
        lambda profile, local_path: (_ for _ in ()).throw(
            ValueError("journal is missing state tabs: Offer")
        ),
    )
    answers = iter([
        "default",
        "https://docs.google.com/document/d/journal",
        "https://drive.google.com/drive/folders/root",
        "",
        "IL",
        "https://calendly.com/example/intro",
        str(resume_path),
        "+972502345678",
    ])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert onboarding.main() == 1
    assert not runtime_path.exists()
