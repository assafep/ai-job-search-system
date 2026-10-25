import json

import pytest

from application_service.google_docs_repository import GoogleDocsRepository


class FakeArtifacts:
    def __init__(self, profile):
        self.profile = profile
        self.read_names = []

    def read_root(self, filename):
        self.read_names.append(filename)
        return json.dumps(self.profile).encode("utf-8")

    def read_path(self, path):
        raise KeyError(path)


class LegacyProfileArtifacts(FakeArtifacts):
    def read_root(self, filename):
        self.read_names.append(filename)
        if filename == "applicant_profile.json":
            return None
        return json.dumps(self.profile).encode("utf-8")


def test_google_profile_read_validates_envelope():
    repository = GoogleDocsRepository("token", "document", "https://drive.google.com/drive/folders/root")
    repository.artifacts = FakeArtifacts({"schema_version": 1})
    with pytest.raises(ValueError):
        repository.get_profile()
    assert repository.artifacts.read_names == ["applicant_profile.json"]


def test_google_profile_read_accepts_existing_legacy_filename():
    profile = {
        "schema_version": 1,
        "personal": {},
        "professional": {},
        "employment": [],
    }
    repository = GoogleDocsRepository("token", "document", "https://drive.google.com/drive/folders/root")
    repository.artifacts = LegacyProfileArtifacts(profile)

    assert repository.get_profile() == profile
    assert repository.artifacts.read_names == ["applicant_profile.json", "application_profile.json"]


def test_profile_write_renames_legacy_file_in_place():
    profile = {'schema_version': 1, 'personal': {'name': 'Candidate'}, 'professional': {}, 'employment': []}
    calls = []
    class Artifacts:
        root_folder_id = 'root'
        def migrate_profile(self):
            calls.append(('migrate',))
        def save_path(self, path, raw, mime):
            calls.append(('save', path, json.loads(raw)))
            return {'id': 'existing-profile-id', 'name': 'applicant_profile.json'}
    repository = GoogleDocsRepository('token', 'document')
    repository.artifacts = Artifacts()
    result = repository.save_profile(profile)
    assert result['id'] == 'existing-profile-id'
    assert calls[0] == ('migrate',)
    assert calls[1] == ('save', 'Applicant/applicant_profile.json', profile)
