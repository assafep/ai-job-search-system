from services.onboarding import main as onboarding


def test_sync_without_credentials_is_explicit(monkeypatch):
    monkeypatch.delenv("GOOGLE_ACCESS_TOKEN", raising=False)
    assert onboarding.sync_profile_to_drive({"schema_version": 1}) == "authentication not configured"


def test_sync_reports_partial_refresh_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "refresh")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)

    try:
        onboarding.sync_profile_to_drive({"schema_version": 1})
    except RuntimeError as error:
        assert "GOOGLE_CLIENT_ID" in str(error)
    else:
        raise AssertionError("partial refresh credentials should fail clearly")


def test_sync_validates_journal_before_writing_profile(monkeypatch):
    class InvalidJournalRepository:
        def journal_status(self):
            return {
                "valid": False,
                "required_states": ["Bookmarked", "Offer"],
                "missing_states": ["Offer"],
                "duplicate_states": [],
            }

        def get_profile(self):
            raise AssertionError("profile lookup must not happen after journal failure")

    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "token")
    monkeypatch.setattr(
        "persistence_api.google_docs_repository.repository_from_environment",
        lambda: InvalidJournalRepository(),
    )

    try:
        onboarding.sync_profile_to_drive({"schema_version": 1})
    except ValueError as error:
        assert "missing: Offer" in str(error)
    else:
        raise AssertionError("invalid journal should stop onboarding sync")
