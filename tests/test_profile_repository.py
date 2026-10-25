from application_service.repository import MemoryRepository


def test_memory_profile_round_trip():
    repository = MemoryRepository()
    profile = {
        "schema_version": 1,
        "personal": {"full_name": "Example"},
        "professional": {},
        "employment": [],
    }

    assert repository.get_profile() is None
    assert repository.save_profile(profile) == profile
    assert repository.get_profile() == profile


def test_memory_profile_rejects_incomplete_envelope():
    repository = MemoryRepository()
    try:
        repository.save_profile({"schema_version": 1})
    except ValueError as error:
        assert "professional" in str(error)
    else:
        raise AssertionError("incomplete profile should fail validation")
