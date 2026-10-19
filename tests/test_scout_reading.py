"""Tests for the unified posting_from_model extractor."""

import pytest
from services.scout_reading import posting_from_model, PostingReadFailure, MIN_DESCRIPTION


LONG_DESCRIPTION = "x" * (MIN_DESCRIPTION + 1)


def _flat_output(description=LONG_DESCRIPTION, url="https://example.test/job/1"):
    return {
        "company": "Example",
        "role": "Engineer",
        "posting_url": url,
        "description": description,
        "read_failure": "",
    }


class TestFlatFormat:
    """posting_from_model handles read-posting flat format."""

    def test_returns_posting_from_flat_output(self):
        result = posting_from_model(
            "https://example.test/job/1", "Example", "Engineer",
            _flat_output())
        assert result is not None
        assert result["posting_url"] == "https://example.test/job/1"
        assert result["company"] == "Example"
        assert result["role"] == "Engineer"
        assert len(result["description"]) >= MIN_DESCRIPTION

    def test_preserves_verified_resolved_source_url(self):
        output = _flat_output()
        output["resolved_url"] = "https://jobs.example.test/canonical/1"
        result = posting_from_model(
            "https://example.test/job/1", "Example", "Engineer", output)
        assert result["posting_url"] == "https://example.test/job/1"
        assert result["source_url"] == "https://jobs.example.test/canonical/1"

    def test_raises_on_url_mismatch(self):
        output = _flat_output(url="https://other.test/job/2")
        with pytest.raises(ValueError, match="URL mismatch"):
            posting_from_model(
                "https://example.test/job/1", "Example", "Engineer", output)

    def test_raises_on_short_description(self):
        output = _flat_output(description="too short")
        with pytest.raises(ValueError, match="too short"):
            posting_from_model(
                "https://example.test/job/1", "Example", "Engineer", output)

    def test_raises_on_read_failure(self):
        output = _flat_output()
        output["read_failure"] = "404"
        with pytest.raises(PostingReadFailure, match="404"):
            posting_from_model(
                "https://example.test/job/1", "Example", "Engineer", output)

    def test_raises_on_non_dict_output(self):
        with pytest.raises(ValueError, match="must return an object"):
            posting_from_model(
                "https://example.test/job/1", "Example", "Engineer", "not a dict")
