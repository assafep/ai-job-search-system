"""Build Applicant/applicant_profile.json from the résumé PDF.

The applicant-profile agent copies only facts present in the résumé text.
The caller stores the result. This module does not invent missing facts.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from io import BytesIO
from urllib.parse import urlsplit

PROFILE_PATH = "Applicant/applicant_profile.json"
# Contact details the cover letter and later messages need. The résumé wins when
# it already states one. The wizard asks only for the fields this list leaves blank.
REQUIRED_CONTACTS = (
    {
        "field": "email",
        "title": "Email address",
        "label": "email address",
        "article": "an",
        "env": "AJSS_CONTACT_EMAIL",
    },
    {
        "field": "phone",
        "title": "Phone number",
        "label": "phone number",
        "article": "a",
        "env": "AJSS_CONTACT_PHONE",
    },
)


def pdf_text(content):
    from pypdf import PdfReader
    if not isinstance(content, (bytes, bytearray)) or not content.startswith(b"%PDF-"):
        raise ValueError("The résumé is not a PDF")
    reader = PdfReader(BytesIO(bytes(content)))
    text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    if not text:
        raise ValueError("The résumé PDF has no extractable text")
    return text


def document_from_agent_output(extracted, resume_path, digest):
    if not isinstance(extracted, dict):
        raise ValueError("Profile extraction must return an object")
    personal = dict(extracted.get("personal") or {})
    name = str(personal.get("name") or "").strip()
    if not name:
        raise ValueError("The résumé did not state a name, so the profile was not saved")
    personal["name"] = name
    return {
        "schema_version": 1,
        "updated_on": date.today().isoformat(),
        "personal": personal,
        "professional": dict(extracted.get("professional") or {}),
        "skills": dict(extracted.get("skills") or {}),
        "employment": list(extracted.get("employment") or []),
        "education": list(extracted.get("education") or []),
        "certifications": list(extracted.get("certifications") or []),
        "languages_listed_in_resume": list(extracted.get("languages_listed_in_resume") or []),
        "usage_notes": list(extracted.get("usage_notes") or []),
        "source_resume": {"path": resume_path, "sha256": digest},
    }


def _strings(value):
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if not isinstance(value, list):
        return []
    found = []
    for item in value:
        if isinstance(item, str) and item.strip():
            found.append(item.strip())
        elif isinstance(item, dict):
            label = item.get("name") or item.get("title") or item.get("credential")
            if isinstance(label, str) and label.strip():
                found.append(label.strip())
    return found


def _unique(values):
    seen = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def normalize_extracted(extracted):
    """Fold résumé facts into the applicant-profile field names."""
    if not isinstance(extracted, dict):
        return extracted
    skills = extracted.get("skills")
    technical = []
    other = []
    if isinstance(skills, dict):
        technical.extend(_strings(skills.get("technical")))
        other.extend(_strings(skills.get("other")))
        for key, items in skills.items():
            if key not in {"technical", "other"}:
                technical.extend(_strings(items))
    else:
        technical.extend(_strings(skills))
    jobs = []
    for item in extracted.get("employment") or []:
        if not isinstance(item, dict):
            continue
        job = {}
        for key in ("company", "role", "dates", "location"):
            text = _text(item.get(key))
            if text:
                job[key] = text
        job["accomplishments"] = _strings(item.get("accomplishments"))
        jobs.append(job)
    schools = []
    for item in extracted.get("education") or []:
        if not isinstance(item, dict):
            continue
        qualification = _text(item.get("qualification"))
        if not qualification:
            qualification = " ".join(
                part for part in (_text(item.get("credential")), _text(item.get("field"))) if part
            )
        dates = _text(item.get("dates")) or _text(item.get("year"))
        school = {}
        institution = _text(item.get("institution"))
        if institution:
            school["institution"] = institution
        if qualification:
            school["qualification"] = qualification
        if dates:
            school["dates"] = dates
        schools.append(school)
    personal = extracted.get("personal") if isinstance(extracted.get("personal"), dict) else {}
    professional = extracted.get("professional") if isinstance(extracted.get("professional"), dict) else {}
    normalized = {
        "schema_version": 1,
        "personal": {key: _text(personal.get(key)) for key in (
            "name", "email", "phone", "linkedin_url", "location",
        ) if _text(personal.get(key))},
        "professional": {key: _text(professional.get(key)) for key in ("headline", "summary") if _text(professional.get(key))},
        "skills": {"technical": _unique(technical), "other": _unique(other)},
        "employment": jobs,
        "education": schools,
        "certifications": _strings(extracted.get("certifications")),
        "languages_listed_in_resume": _strings(extracted.get("languages_listed_in_resume")),
    }
    notes = _strings(extracted.get("usage_notes"))
    if notes:
        normalized["usage_notes"] = notes
    return normalized


class _NormalizingProvider:
    def __init__(self, inner):
        self.inner = inner

    def generate(self, prompt, context):
        return normalize_extracted(self.inner.generate(prompt, context))


def agent_extractor(text):
    """Run the applicant-profile prompt. The résumé text is data, not instructions."""
    from adapters.llm.openai_provider import OpenAIProvider
    from ajss.documents.prompts import run
    return run("applicant-profile", {"resume_text": text}, _NormalizingProvider(OpenAIProvider()))


def contact_text(personal, field):
    if not isinstance(personal, dict):
        return ""
    value = personal.get(field)
    if value is None:
        return ""
    return str(value).strip()


def missing_contact_fields(personal):
    return [item["field"] for item in REQUIRED_CONTACTS if not contact_text(personal, item["field"])]


def validate_contact(field, value):
    text = str(value or "").strip()
    if field == "email":
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
            raise ValueError("email address must look like name@example.com")
        return text
    if field == "phone":
        if not re.fullmatch(r"[0-9+().\-\s]{7,40}", text) or sum(ch.isdigit() for ch in text) < 7:
            raise ValueError("phone number must contain at least 7 digits")
        return text
    raise ValueError("unsupported contact detail: " + field)


def validate_calendly_url(value):
    """Accept a public Calendly scheduling link. The résumé extractor does not invent one."""
    text = str(value or "").strip()
    parts = urlsplit(text)
    host = (parts.hostname or "").casefold()
    if parts.scheme != "https" or host not in {"calendly.com", "www.calendly.com"}:
        raise ValueError("calendly_url must be an https://calendly.com/… link")
    if not parts.path.strip("/"):
        raise ValueError("calendly_url must include the scheduling path")
    if parts.username or parts.password:
        raise ValueError("calendly_url must not include a username or password")
    return text


def profile_calendly_url(document):
    return contact_text((document or {}).get("personal") or {}, "calendly_url")


def fill_missing_contacts(document, supplied):
    """Copy supplied values only into contact fields the résumé left blank."""
    personal = dict(document.get("personal") or {})
    sources = dict(document.get("sources") or {})
    origin = dict(sources.get("contacts") or {})
    changed = False
    for item in REQUIRED_CONTACTS:
        field = item["field"]
        if contact_text(personal, field):
            continue
        raw = (supplied or {}).get(field)
        if raw is None or str(raw).strip() == "":
            continue
        personal[field] = validate_contact(field, raw)
        origin[field] = "onboarding"
        changed = True
    updated = dict(document)
    updated["personal"] = personal
    if origin:
        sources["contacts"] = origin
        updated["sources"] = sources
    return updated, changed


def apply_calendly_url(document, calendly_url):
    """Store a scheduling link on the profile. None leaves the saved value unchanged."""
    if calendly_url is None:
        return document, False
    personal = dict(document.get("personal") or {})
    current = contact_text(personal, "calendly_url")
    chosen = validate_calendly_url(calendly_url) if str(calendly_url).strip() else ""
    if chosen == current:
        return document, False
    if chosen:
        personal["calendly_url"] = chosen
    else:
        personal.pop("calendly_url", None)
    updated = dict(document)
    updated["personal"] = personal
    sources = dict(updated.get("sources") or {})
    origin = dict(sources.get("contacts") or {})
    if chosen:
        origin["calendly_url"] = "onboarding"
    else:
        origin.pop("calendly_url", None)
    if origin:
        sources["contacts"] = origin
    else:
        sources.pop("contacts", None)
    if sources:
        updated["sources"] = sources
    else:
        updated.pop("sources", None)
    return updated, True


def _read_profile(store):
    raw = store.read_path(PROFILE_PATH)["content"]
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("Applicant profile must be a JSON object")
    return document


def _store_profile(store, document):
    store.save_path(
        PROFILE_PATH,
        json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8"),
        mime_type="application/json",
    )


def _profile_result(document, digest, written):
    return {
        "written": written,
        "sha256": digest,
        "path": PROFILE_PATH,
        "missing": missing_contact_fields(document.get("personal") or {}),
        "calendly_url": profile_calendly_url(document),
    }


def patch_profile_contacts(store, contacts, calendly_url=None):
    """Fill blank contact fields on the saved profile. Does not read the résumé."""
    document = _read_profile(store)
    source = document.get("source_resume") if isinstance(document.get("source_resume"), dict) else {}
    digest = str(source.get("sha256") or "")
    if not digest:
        raise KeyError(PROFILE_PATH)
    document, filled = fill_missing_contacts(document, contacts or {})
    document, scheduled = apply_calendly_url(document, calendly_url)
    written = False
    if filled or scheduled:
        _store_profile(store, document)
        written = True
    return _profile_result(document, digest, written)


def ensure_applicant_profile(store, resume_path, extractor=agent_extractor, contacts=None, calendly_url=None):
    """Write the profile when the résumé changed. Supplied contacts fill blanks only."""
    pdf = store.read_path(resume_path)["content"]
    digest = hashlib.sha256(pdf).hexdigest()
    try:
        document = _read_profile(store)
    except KeyError:
        document = None
    source = (document or {}).get("source_resume") or {}
    if document is not None and (source.get("sha256"), source.get("path")) == (digest, resume_path):
        written = False
    else:
        document = document_from_agent_output(extractor(pdf_text(pdf)), resume_path, digest)
        written = True
    document, filled = fill_missing_contacts(document, contacts or {})
    document, scheduled = apply_calendly_url(document, calendly_url)
    if written or filled or scheduled:
        _store_profile(store, document)
        written = True
    return _profile_result(document, digest, written)
