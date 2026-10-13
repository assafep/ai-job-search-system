import json


def test_applicant_profile_schema_declares_stable_envelope():
    with open("schemas/applicant-profile.schema.json") as handle:
        schema = json.load(handle)
    assert schema["required"] == ["schema_version", "personal", "professional", "employment"]
    assert schema["additionalProperties"] is True
