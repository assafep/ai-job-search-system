"""Validation for the provider-neutral applicant profile envelope."""

from urllib.parse import urlsplit
import re


REQUIRED_FIELDS = ("schema_version", "personal", "professional", "employment")


def validate_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError("profile must be a JSON object")
    missing = [field for field in REQUIRED_FIELDS if field not in profile]
    if missing:
        raise ValueError("profile is missing required fields: " + ", ".join(missing))
    if not isinstance(profile["schema_version"], int) or profile["schema_version"] < 1:
        raise ValueError("profile schema_version must be a positive integer")
    for field in ("personal", "professional"):
        if not isinstance(profile[field], dict):
            raise ValueError("profile {} must be an object".format(field))
    if not isinstance(profile["employment"], list):
        raise ValueError("profile employment must be an array")
    job_search = profile.get("job_search")
    if job_search is not None:
        if not isinstance(job_search, dict):
            raise ValueError("profile job_search must be an object")
        for field in ("countries", "search_terms"):
            values = job_search.get(field, [])
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError("profile job_search.{} must be an array of non-empty strings".format(field))
        if any(not re.match(r"^[A-Z]{2}$", value) for value in job_search.get("countries", [])):
            raise ValueError("profile job_search.countries must contain ISO-style country codes")
    for container_name in ("application_defaults", "personal"):
        container = profile.get(container_name)
        if container is not None and not isinstance(container, dict):
            raise ValueError("profile {} must be an object".format(container_name))
        if isinstance(container, dict) and container.get("calendly_url") is not None:
            value = container["calendly_url"]
            parsed = urlsplit(value.strip()) if isinstance(value, str) else None
            if not parsed or parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                raise ValueError("profile {}.calendly_url must be a valid HTTP URL".format(container_name))
    return profile


def calendly_url(profile):
    """Return the scheduling URL from current or legacy profile layouts."""
    if not isinstance(profile, dict):
        return None
    defaults = profile.get("application_defaults")
    if isinstance(defaults, dict) and defaults.get("calendly_url"):
        return defaults["calendly_url"]
    personal = profile.get("personal")
    if isinstance(personal, dict) and personal.get("calendly_url"):
        return personal["calendly_url"]
    return None
