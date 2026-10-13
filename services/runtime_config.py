"""Loading and validating local runtime configuration."""

import json
import os
import re
from urllib.parse import urlsplit


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_PATH = os.path.join(ROOT, "config", "runtime.json")


class OnboardingRequired(RuntimeError):
    pass


def _google_doc_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value.strip())
    return (
        parsed.scheme.lower() in {"http", "https"}
        and parsed.netloc.lower() == "docs.google.com"
        and bool(re.match(r"^/document/(?:u/\d+/)?d/[^/]+", parsed.path))
    )


def _drive_folder_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value.strip())
    return (
        parsed.scheme.lower() in {"http", "https"}
        and parsed.netloc.lower() == "drive.google.com"
        and bool(re.match(r"^/drive/(?:u/\d+/)?folders/[^/]+", parsed.path))
    )


def load(path=None, require=True):
    """Load runtime configuration, honoring the deployment path override."""
    path = path or os.environ.get("RUNTIME_CONFIG_PATH") or DEFAULT_PATH
    if not os.path.exists(path):
        if require:
            raise OnboardingRequired(
                "config/runtime.json is missing; run: python3 -m services.onboarding"
            )
        return {}
    with open(path) as handle:
        config = json.load(handle)
    validate(config)
    return config


def validate(config):
    if not isinstance(config, dict):
        raise ValueError("runtime configuration must be a JSON object")
    drive = config.get("google_drive")
    required = ["connection", "interview_journal_url", "artifact_root_url", "applicant_profile_path"]
    missing = [name for name in required if not isinstance(drive, dict) or not drive.get(name)]
    if missing:
        raise ValueError("runtime configuration is missing: " + ", ".join(missing))
    if not _google_doc_url(drive["interview_journal_url"]):
        raise ValueError("google_drive.interview_journal_url must be a Google Docs URL")
    if not _drive_folder_url(drive["artifact_root_url"]):
        raise ValueError("google_drive.artifact_root_url must be a Google Drive folder URL")
    profile_path = os.path.expanduser(drive["applicant_profile_path"])
    if not os.path.isabs(profile_path):
        profile_path = os.path.join(ROOT, profile_path)
    if not os.path.isfile(profile_path):
        raise ValueError("applicant profile file does not exist: " + profile_path)
    application_root_path = drive.get("application_root_path", "")
    if not isinstance(application_root_path, str):
        raise ValueError("google_drive.application_root_path must be a relative path")
    if application_root_path:
        from persistence_api.file_paths import normalize_file_path
        try:
            normalize_file_path(application_root_path)
        except ValueError:
            raise ValueError("google_drive.application_root_path must be a relative path")
    scheduling = config.get("scheduling", {})
    if not isinstance(scheduling, dict):
        raise ValueError("scheduling configuration must be an object")
    if "calendly_url" in scheduling:
        value = scheduling["calendly_url"]
        parsed = urlsplit(value.strip()) if isinstance(value, str) else None
        if not parsed or parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("scheduling.calendly_url must be a valid HTTP URL")


def calendly_url(config):
    """Return the configured scheduling URL, if onboarding supplied one."""
    return (config.get("scheduling") or {}).get("calendly_url") if isinstance(config, dict) else None
