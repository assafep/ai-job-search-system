"""The applicant profile is copied from the résumé and is not rewritten unchanged."""
import json

import pytest

from ajss.documents.generator import _pdf_bytes
from ajss.onboard.profile import (
    PROFILE_PATH,
    document_from_agent_output,
    ensure_applicant_profile,
    pdf_text,
)


class MemoryStore:
    def __init__(self, files):
        self.files = files

    def read_path(self, path):
        if path not in self.files:
            raise KeyError(path)
        return {"content": self.files[path]}

    def save_path(self, path, content, mime_type=None, idempotency_key=None):
        self.files[path] = content if isinstance(content, bytes) else content.encode("utf-8")
        return {"id": path}


def extracted(name="Ada Applicant"):
    return {
        "personal": {"name": name, "email": "ada@example.test"},
        "professional": {"headline": "Engineer"},
        "skills": {"technical": ["Python"]},
        "employment": [{"company": "Example", "role": "Engineer", "dates": "2020", "accomplishments": []}],
        "education": [],
        "certifications": [],
        "languages_listed_in_resume": [],
        "usage_notes": [],
    }


def test_xai_key_uses_the_grok_chat_endpoint(monkeypatch):
    from adapters.llm.openai_provider import OpenAIProvider
    for name in ("OPENAI_API_KEY", "AJSS_LLM_API_KEY", "AJSS_LLM_PROVIDER", "AJSS_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    provider = OpenAIProvider()
    assert provider.api_key == "xai-test"
    assert provider.endpoint == "https://api.x.ai/v1/chat/completions"
    assert provider.model == "grok-4.7"


def test_pdf_text_is_taken_from_the_resume():
    text = pdf_text(_pdf_bytes(["Ada Applicant", "ada@example.test"]))
    assert "Ada Applicant" in text
    assert "ada@example.test" in text


def test_profile_is_written_once_for_the_same_pdf():
    pdf = _pdf_bytes(["Ada Applicant"])
    store = MemoryStore({"Applicant/resume.pdf": pdf})
    calls = []

    def extractor(text):
        calls.append(text)
        return extracted()

    first = ensure_applicant_profile(store, "Applicant/resume.pdf", extractor)
    second = ensure_applicant_profile(store, "Applicant/resume.pdf", extractor)
    assert first["written"] is True
    assert second["written"] is False
    assert calls == [pdf_text(pdf)]
    saved = json.loads(store.files[PROFILE_PATH].decode("utf-8"))
    assert saved["personal"]["name"] == "Ada Applicant"
    assert saved["source_resume"]["path"] == "Applicant/resume.pdf"
    assert saved["source_resume"]["sha256"] == first["sha256"]


def test_a_resume_without_a_name_is_not_saved():
    with pytest.raises(ValueError, match="did not state a name"):
        document_from_agent_output({"personal": {}}, "Applicant/resume.pdf", "abc")


def test_resume_facts_under_other_names_fit_the_profile_schema():
    from ajss.documents.prompts import run
    from ajss.onboard.profile import _NormalizingProvider
    raw = {
        "schema_version": 1,
        "personal": {"name": "Ada Applicant", "email": "ada@example.test"},
        "professional": {"headline": "Engineer"},
        "skills": {
            "Languages & Frameworks": ["Python"],
            "Databases": ["PostgreSQL"],
            "technical": ["Go"],
        },
        "employment": [{
            "company": "Example",
            "role": "Engineer",
            "dates": "2020",
            "location": "Remote",
            "accomplishments": ["Shipped a service"],
        }],
        "education": [{
            "institution": "Example University",
            "credential": "BSc",
            "field": "Computer Science",
            "year": 2016,
        }],
        "certifications": [],
        "languages_listed_in_resume": ["English"],
    }

    class Provider:
        def generate(self, prompt, context):
            return raw

    saved = run("applicant-profile", {"resume_text": "fixture"}, _NormalizingProvider(Provider()))
    assert saved["skills"]["technical"] == ["Go", "Python", "PostgreSQL"]
    assert saved["employment"][0]["location"] == "Remote"
    assert saved["education"][0]["qualification"] == "BSc Computer Science"
    assert saved["education"][0]["dates"] == "2016"
    assert "credential" not in saved["education"][0]


def test_supplied_phone_fills_a_blank_and_does_not_replace_the_resume_email():
    pdf = _pdf_bytes(["Ada Applicant"])
    store = MemoryStore({"Applicant/resume.pdf": pdf})
    first = ensure_applicant_profile(
        store, "Applicant/resume.pdf", lambda text: extracted(),
        contacts={"phone": "+1-555-0100", "email": "other@example.test"},
    )
    saved = json.loads(store.files[PROFILE_PATH].decode("utf-8"))
    assert saved["personal"]["email"] == "ada@example.test"
    assert saved["personal"]["phone"] == "+1-555-0100"
    assert saved["sources"]["contacts"]["phone"] == "onboarding"
    assert "email" not in saved["sources"]["contacts"]
    assert first["missing"] == []
    second = ensure_applicant_profile(
        store, "Applicant/resume.pdf", lambda text: extracted(),
        contacts={"phone": "+1-555-0100"},
    )
    assert second["written"] is False
    assert second["missing"] == []


def test_cover_letter_includes_the_phone_and_calendly_link():
    from io import BytesIO
    from pypdf import PdfReader
    from ajss.documents.generator import generate_application_pdf
    letter = "Dear Hiring Team,\n\nI am applying for this role and can start soon.\n\nAda Applicant"
    package = generate_application_pdf(
        letter,
        resume_pdf=_pdf_bytes(["Ada Applicant"]),
        applicant_profile={"personal": {
            "name": "Ada Applicant",
            "email": "ada@example.test",
            "phone": "+1-555-0100",
        }},
        calendly_url="https://calendly.com/ada/intro",
        application={"company": "Example", "role": "Engineer"},
    )
    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(package)).pages)
    assert "+1-555-0100" in text
    assert "ada@example.test" in text
    assert "https://calendly.com/ada/intro" in text
    from_profile = generate_application_pdf(
        letter,
        resume_pdf=_pdf_bytes(["Ada Applicant"]),
        applicant_profile={"personal": {
            "name": "Ada Applicant",
            "email": "ada@example.test",
            "phone": "+1-555-0100",
            "calendly_url": "https://calendly.com/ada/from-profile",
        }},
        application={"company": "Example", "role": "Engineer"},
    )
    profile_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(from_profile)).pages)
    assert "https://calendly.com/ada/from-profile" in profile_text


def test_contact_fill_does_not_read_the_resume():
    from ajss.onboard.profile import patch_profile_contacts
    profile = {
        "schema_version": 1,
        "personal": {"name": "Ada Applicant", "email": "ada@example.test"},
        "source_resume": {"path": "Applicant/resume.pdf", "sha256": "abc"},
    }
    store = MemoryStore({PROFILE_PATH: json.dumps(profile).encode()})

    def read_path(path):
        if str(path).endswith(".pdf"):
            raise AssertionError("resume was read")
        return MemoryStore.read_path(store, path)

    store.read_path = read_path
    result = patch_profile_contacts(store, {"phone": "+1-555-0100", "email": "other@example.test"})
    saved = json.loads(store.files[PROFILE_PATH].decode())
    assert saved["personal"]["email"] == "ada@example.test"
    assert saved["personal"]["phone"] == "+1-555-0100"
    assert result["written"] is True
    assert result["missing"] == []
    assert patch_profile_contacts(store, {"phone": "+1-555-0100"})["written"] is False


def test_calendly_link_is_written_onto_the_profile_without_reading_the_resume():
    from ajss.onboard.profile import patch_profile_contacts
    profile = {
        "schema_version": 1,
        "personal": {"name": "Ada Applicant", "email": "ada@example.test", "phone": "+1-555-0100"},
        "source_resume": {"path": "Applicant/resume.pdf", "sha256": "abc"},
    }
    store = MemoryStore({PROFILE_PATH: json.dumps(profile).encode()})

    def read_path(path):
        if str(path).endswith(".pdf"):
            raise AssertionError("resume was read")
        return MemoryStore.read_path(store, path)

    store.read_path = read_path
    link = "https://calendly.com/ada/intro"
    result = patch_profile_contacts(store, {}, calendly_url=link)
    saved = json.loads(store.files[PROFILE_PATH].decode())
    assert saved["personal"]["calendly_url"] == link
    assert saved["sources"]["contacts"]["calendly_url"] == "onboarding"
    assert result["written"] is True
    assert result["calendly_url"] == link
    assert patch_profile_contacts(store, {}, calendly_url=link)["written"] is False
    cleared = patch_profile_contacts(store, {}, calendly_url="")
    assert cleared["written"] is True
    assert cleared["calendly_url"] == ""
    assert "calendly_url" not in json.loads(store.files[PROFILE_PATH].decode())["personal"]
