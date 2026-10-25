import json

from services.onboarding import main as onboarding


def test_existing_drive_profile_is_copied_to_local_file(monkeypatch, tmp_path):
    local_path = tmp_path / "applicant_profile.json"
    existing = {
        "schema_version": 1,
        "personal": {"full_name": "Existing"},
        "professional": {},
        "employment": [],
    }

    class FakeRepository:
        def journal_status(self):
            return {"valid": True, "required_states": [], "missing_states": [], "duplicate_states": []}

        def get_profile(self):
            return existing

        def save_profile(self, profile):
            raise AssertionError("existing profile should not be overwritten")

    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "token")
    monkeypatch.setattr(onboarding, "repository_from_environment", FakeRepository, raising=False)

    # Import is intentionally patched at the module boundary used by the helper.
    import application_service.google_docs_repository as google_repository
    monkeypatch.setattr(google_repository, "repository_from_environment", lambda: FakeRepository())
    result = onboarding.sync_profile_to_drive({"schema_version": 1}, str(local_path))

    assert result == "existing Drive profile retained and copied locally"
    assert json.loads(local_path.read_text()) == existing


def test_confirmed_phone_is_saved_into_existing_drive_profile(monkeypatch, tmp_path):
    existing = {'schema_version': 1, 'personal': {'name': 'Existing'},
                'professional': {}, 'employment': [{'company': 'Keep me'}]}
    saved = []
    class Repository:
        def get_profile(self): return existing
        def save_profile(self, profile): saved.append(profile)
    monkeypatch.setenv('GOOGLE_ACCESS_TOKEN', 'token')
    monkeypatch.setattr('application_service.google_docs_repository.repository_from_environment', Repository)
    path = tmp_path / 'profile.json'
    onboarding.sync_profile_to_drive({'personal': {'phone': '+972502345678'}}, str(path))
    assert saved[0]['personal']['phone'] == '+972502345678'
    assert saved[0]['employment'] == [{'company': 'Keep me'}]
    assert json.loads(path.read_text())['personal']['phone'] == '+972502345678'
