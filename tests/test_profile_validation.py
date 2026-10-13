import pytest

from persistence_api.profile import calendly_url, validate_profile


def test_profile_calendly_url_supports_current_and_legacy_layouts():
    assert calendly_url({"application_defaults": {"calendly_url": "https://calendly.com/current"}}) == (
        "https://calendly.com/current"
    )
    assert calendly_url({"personal": {"calendly_url": "https://calendly.com/legacy"}}) == (
        "https://calendly.com/legacy"
    )
    assert calendly_url({"application_defaults": {}, "personal": {}}) is None


def test_profile_validation_accepts_onboarding_shape():
    profile = {
        "schema_version": 1,
        "personal": {},
        "professional": {},
        "employment": [],
        "job_search": {"countries": ["IL"], "search_terms": []},
    }
    assert validate_profile(profile) is profile


@pytest.mark.parametrize("profile", [None, {}, {"schema_version": 1, "personal": {}, "professional": {}, "employment": {}}])
def test_profile_validation_rejects_invalid_shapes(profile):
    with pytest.raises(ValueError):
        validate_profile(profile)


def test_profile_validation_rejects_malformed_job_search():
    profile = {
        "schema_version": 1,
        "personal": {},
        "professional": {},
        "employment": [],
        "job_search": {"countries": ["Israel"], "search_terms": []},
    }
    with pytest.raises(ValueError, match="job_search.countries"):
        validate_profile(profile)


@pytest.mark.parametrize("container_name", ["personal", "application_defaults"])
def test_profile_validation_rejects_malformed_calendly_url(container_name):
    profile = {
        "schema_version": 1,
        "personal": {},
        "professional": {},
        "employment": [],
        container_name: {"calendly_url": "not-a-url"},
    }
    with pytest.raises(ValueError, match="calendly_url"):
        validate_profile(profile)
