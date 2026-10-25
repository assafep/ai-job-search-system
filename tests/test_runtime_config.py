import json
import os

from services.runtime_config import OnboardingRequired, load, validate


def test_missing_runtime_config_requires_onboarding(tmpdir):
    missing = os.path.join(str(tmpdir), "runtime.json")
    try:
        load(missing)
    except OnboardingRequired:
        return
    raise AssertionError("missing configuration should require onboarding")


def test_runtime_config_requires_drive_settings():
    try:
        validate({"google_drive": {"connection": "default"}})
    except ValueError as error:
        assert "interview_journal_url" in str(error)
        return
    raise AssertionError("incomplete configuration should fail validation")


def test_runtime_config_loads_valid_json(tmpdir):
    path = os.path.join(str(tmpdir), "runtime.json")
    profile_path = os.path.join(str(tmpdir), "applicant_profile.json")
    with open(profile_path, "w") as handle:
        json.dump({"schema_version": 1}, handle)
    with open(path, "w") as handle:
        json.dump({"google_drive": {
            "connection": "default",
            "interview_journal_url": "https://docs.google.com/document/d/example",
            "artifact_root_url": "https://drive.google.com/drive/u/0/folders/example",
            "applicant_profile_path": profile_path,
        }}, handle)
    assert load(path)["google_drive"]["connection"] == "default"


def test_runtime_config_resolves_relative_profile_path(tmpdir, monkeypatch):
    profile_path = os.path.join(str(tmpdir), "applicant_profile.json")
    with open(profile_path, "w") as handle:
        json.dump({"schema_version": 1}, handle)
    monkeypatch.setattr("services.runtime_config.ROOT", str(tmpdir))
    config = {"google_drive": {
        "connection": "default",
        "interview_journal_url": "https://docs.google.com/document/d/example",
        "artifact_root_url": "https://drive.google.com/drive/folders/example",
            "applicant_profile_path": "applicant_profile.json",
    }}
    assert validate(config) is None


def test_bookmark_reader_uses_only_its_runtime_section(tmp_path, monkeypatch):
    from services.bookmark_read import configured_reader

    path = os.path.join(str(tmp_path), "runtime.json")
    profile_path = os.path.join(str(tmp_path), "applicant_profile.json")
    with open(profile_path, "w") as handle:
        json.dump({"schema_version": 1}, handle)
    with open(path, "w") as handle:
        json.dump({"google_drive": {
            "connection": "default",
            "interview_journal_url": "https://docs.google.com/document/d/example",
            "artifact_root_url": "https://drive.google.com/drive/folders/example",
            "applicant_profile_path": profile_path,
        }, "scout": {"coding_agent": "codex"}, "bookmark-discovered": {
            "coding_agent": "grok", "model": "grok-4.7", "effort": "medium"}}, handle)
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", path)
    reader, model, effort = configured_reader()
    assert reader._executable() == "grok"
    assert model == "grok-4.7"
    assert effort == "medium"
    assert reader.reasoning_effort == "medium"


def test_bookmark_section_must_name_model_and_effort():
    try:
        validate({"google_drive": {
            "connection": "default",
            "interview_journal_url": "https://docs.google.com/document/d/example",
            "artifact_root_url": "https://drive.google.com/drive/folders/example",
            "applicant_profile_path": "config/applicant_profile.json",
        }, "bookmark-discovered": {"coding_agent": "grok"}})
    except ValueError as error:
        assert "bookmark-discovered.model" in str(error)
        return
    raise AssertionError("incomplete bookmark settings should fail validation")


def test_runtime_config_reads_optional_calendly_url():
    from services.runtime_config import calendly_url

    assert calendly_url({"scheduling": {"calendly_url": "https://calendly.com/example"}}) == (
        "https://calendly.com/example"
    )
    assert calendly_url({}) is None


def test_runtime_config_rejects_malformed_calendly_url(tmpdir):
    profile_path = os.path.join(str(tmpdir), "applicant_profile.json")
    with open(profile_path, "w") as handle:
        json.dump({"schema_version": 1}, handle)
    config = {"google_drive": {
        "connection": "default",
        "interview_journal_url": "https://docs.google.com/document/d/example",
        "artifact_root_url": "https://drive.google.com/drive/folders/example",
        "applicant_profile_path": profile_path,
    }, "scheduling": {"calendly_url": "https://"}}
    try:
        validate(config)
    except ValueError as error:
        assert "valid HTTP URL" in str(error)
    else:
        raise AssertionError("malformed scheduling URL should fail validation")


def test_runtime_config_rejects_non_object_document():
    try:
        validate([])
    except ValueError as error:
        assert "JSON object" in str(error)
    else:
        raise AssertionError("non-object runtime configuration should fail")


def test_runtime_config_accepts_relative_application_root_path(tmpdir):
    profile_path = os.path.join(str(tmpdir), "applicant_profile.json")
    with open(profile_path, "w") as handle:
        json.dump({"schema_version": 1}, handle)
    config = {"google_drive": {
        "connection": "default",
        "interview_journal_url": "https://docs.google.com/document/d/example",
        "artifact_root_url": "https://drive.google.com/drive/folders/example",
        "applicant_profile_path": profile_path,
        "application_root_path": "applications/active",
    }}
    assert validate(config) is None


def test_runtime_config_rejects_non_google_resource_urls(tmpdir):
    profile_path = os.path.join(str(tmpdir), "applicant_profile.json")
    with open(profile_path, "w") as handle:
        json.dump({"schema_version": 1}, handle)
    config = {"google_drive": {
        "connection": "default",
        "interview_journal_url": "https://example.test/journal",
        "artifact_root_url": "https://drive.google.com/drive/folders/example",
        "applicant_profile_path": profile_path,
    }}
    try:
        validate(config)
    except ValueError as error:
        assert "interview_journal_url" in str(error)
    else:
        raise AssertionError("non-Google journal URL should be rejected")

    config["google_drive"]["interview_journal_url"] = "https://docs.google.com/document/d/example"
    config["google_drive"]["artifact_root_url"] = "https://docs.google.com/document/d/not-a-folder"
    try:
        validate(config)
    except ValueError as error:
        assert "artifact_root_url" in str(error)
    else:
        raise AssertionError("non-Drive artifact URL should be rejected")


def test_runtime_config_rejects_escaping_application_root_path(tmpdir):
    profile_path = os.path.join(str(tmpdir), "applicant_profile.json")
    with open(profile_path, "w") as handle:
        json.dump({"schema_version": 1}, handle)
    config = {"google_drive": {
        "connection": "default",
        "interview_journal_url": "https://docs.google.com/document/d/example",
        "artifact_root_url": "https://drive.google.com/drive/folders/example",
        "applicant_profile_path": profile_path,
        "application_root_path": "applications/../outside",
    }}
    try:
        validate(config)
    except ValueError as error:
        assert "relative path" in str(error)
    else:
        raise AssertionError("escaping application root path should fail")


def test_runtime_config_rejects_dot_application_root_path(tmpdir):
    profile_path = os.path.join(str(tmpdir), "applicant_profile.json")
    with open(profile_path, "w") as handle:
        json.dump({"schema_version": 1}, handle)
    config = {"google_drive": {
        "connection": "default",
        "interview_journal_url": "https://docs.google.com/document/d/example",
        "artifact_root_url": "https://drive.google.com/drive/folders/example",
        "applicant_profile_path": profile_path,
        "application_root_path": ".",
    }}
    try:
        validate(config)
    except ValueError as error:
        assert "relative path" in str(error)
    else:
        raise AssertionError("dot application root path should fail")
