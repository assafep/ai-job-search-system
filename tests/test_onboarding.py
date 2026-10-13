import json

from services.onboarding.main import create_applicant_profile


def test_onboarding_creates_profile_scaffold_from_resume(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"resume")
    profile_path = tmp_path / "applicant_profile.json"

    create_applicant_profile(str(resume), str(profile_path))

    profile = json.loads(profile_path.read_text())
    assert profile["schema_version"] == 1
    assert profile["source_resume"]["file_name"] == "resume.pdf"
    assert profile["source_resume"]["path"] == str(resume.resolve())
    assert "personal" in profile
    assert "professional" in profile
