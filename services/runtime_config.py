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
        from application_service.file_paths import normalize_file_path
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
    _optional_log(config, "activity_log")
    _optional_log(config, "api_log")
    scout = config.get('scout', {})
    if not isinstance(scout, dict):
        raise ValueError('scout configuration must be an object')
    for key in ('coding_agent', 'model'):
        if key in scout and (not isinstance(scout[key], str) or not scout[key].strip()):
            raise ValueError('scout.' + key + ' must be a non-empty string')
    bookmark = config.get('bookmark-discovered')
    if bookmark is not None:
        if not isinstance(bookmark, dict):
            raise ValueError('bookmark-discovered configuration must be an object')
        for key in ('coding_agent', 'model'):
            value = bookmark.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError('bookmark-discovered.' + key + ' must be a non-empty string')
        if bookmark.get('effort') not in {'low', 'medium', 'high', 'xhigh'}:
            raise ValueError('bookmark-discovered.effort must be low, medium, high, or xhigh')


def _optional_log(config, name):
    section = config.get(name, {})
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ValueError(name + " configuration must be an object")
    enabled = section.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(name + ".enabled must be true or false")
    path = section.get("path")
    if enabled and (not isinstance(path, str) or not path.strip()):
        raise ValueError(name + ".path is required when logging is enabled")
    if path is not None and not isinstance(path, str):
        raise ValueError(name + ".path must be a file path")


def calendly_url(config):
    """Return the configured scheduling URL, if onboarding supplied one."""
    return (config.get("scheduling") or {}).get("calendly_url") if isinstance(config, dict) else None
