import json
import pytest

from persistence_api.google_auth import GoogleAccessToken, token_from_environment
from persistence_api.google_docs_repository import repository_from_environment


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


def test_direct_access_token_is_preferred():
    assert token_from_environment({"GOOGLE_ACCESS_TOKEN": "direct"}) == "direct"


def test_refresh_provider_renews_and_caches_access_token():
    calls = []

    def opener(request):
        calls.append(request)
        return FakeResponse({"access_token": "refreshed", "expires_in": 3600})

    provider = GoogleAccessToken("refresh", "client", "secret", opener=opener)
    assert provider.token() == "refreshed"
    assert provider.token() == "refreshed"
    assert len(calls) == 1
    assert "grant_type=refresh_token" in calls[0].data.decode("utf-8")


def test_refresh_credentials_create_provider():
    provider = token_from_environment({
        "GOOGLE_REFRESH_TOKEN": "refresh",
        "GOOGLE_CLIENT_ID": "client",
        "GOOGLE_CLIENT_SECRET": "secret",
    })
    assert isinstance(provider, GoogleAccessToken)


def test_partial_refresh_credentials_are_rejected():
    with pytest.raises(RuntimeError, match="requires GOOGLE_REFRESH_TOKEN"):
        token_from_environment({"GOOGLE_REFRESH_TOKEN": "refresh"})


def test_existing_invalid_runtime_configuration_is_not_ignored(tmp_path, monkeypatch):
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps({
        "google_drive": {
            "connection": "default",
            "interview_journal_url": "https://example.test/not-a-doc",
            "artifact_root_url": "https://drive.google.com/drive/folders/root",
            "applicant_profile_path": str(tmp_path / "applicant_profile.json"),
        }
    }))
    (tmp_path / "applicant_profile.json").write_text("{}")
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(runtime_path))
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "token")
    monkeypatch.delenv("GOOGLE_INTERVIEW_JOURNAL_URL", raising=False)
    monkeypatch.delenv("GOOGLE_ARTIFACT_ROOT_URL", raising=False)

    with pytest.raises(ValueError, match="interview_journal_url"):
        repository_from_environment()
