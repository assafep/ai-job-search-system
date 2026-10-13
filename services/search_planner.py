"""Resume-based discovery planning without side effects."""


SEARCH_PLAN_SCHEMA = {
    "type": "object",
    "required": ["search_terms"],
    "properties": {
        "search_terms": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}


PROMPT = """Derive a compact job-search plan from the applicant profile and resume.
Read the resume file referenced by applicant_profile.source_resume.path when it
is available. Return 5-12 concrete search terms covering likely role titles,
specializations, and important technical domains. Do not invent experience;
use only evidence from the resume. These terms are used to query job sources.
"""


def derive_search_terms(profile, provider):
    if not isinstance(profile, dict):
        return []
    if not hasattr(provider, "generate_with_schema"):
        return []
    result = provider.generate_with_schema(
        PROMPT,
        {"applicant_profile": profile},
        SEARCH_PLAN_SCHEMA,
    )
    terms = result.get("search_terms", []) if isinstance(result, dict) else []
    return [term.strip() for term in terms if isinstance(term, str) and term.strip()][:12]
