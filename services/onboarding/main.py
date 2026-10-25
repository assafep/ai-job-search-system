"""First-run onboarding for local, non-secret runtime configuration."""

import json
import os
import re
import sys
import zipfile
from pypdf.errors import PdfReadError
from datetime import date
from urllib.parse import urlsplit
from .resume_contacts import resume_text, extract_contacts, normalize_phone


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "runtime.json")
BOOKMARK_DISCOVERED = {
    "coding_agent": "grok",
    "model": "grok-4.7",
    "effort": "medium",
}
PROFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "applicant_profile.json")
WEB_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
DOC_URL_RE = re.compile(r"^https?://docs\.google\.com/document/(?:u/\d+/)?d/[^/?]+", re.IGNORECASE)
FOLDER_URL_RE = re.compile(r"^https?://drive\.google\.com/drive/(?:u/\d+/)?folders/[^/?]+", re.IGNORECASE)


def ask(label, validator=None):
    while True:
        value = input(label).strip()
        if validator is None or validator(value):
            return value
        print("Please enter a valid value.")


def existing_path(value):
    return os.path.isfile(os.path.expanduser(value))


def extract_resume_contacts(resume_path, countries=None):
    try:
        return extract_contacts(resume_text(os.path.expanduser(resume_path)), countries)
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile, PdfReadError):
        print('Could not extract resume contact details; enter your phone number below.')
        return {}


def confirm_phone(default=None, countries=None):
    """Always confirm the extracted number; Enter accepts only a valid default."""
    default = normalize_phone(default, countries)
    label = 'Phone number [{}] (Enter to confirm): '.format(default) if default else 'Phone number (include country code, e.g. +972…): '
    while True:
        value = input(label).strip() or default
        normalized = normalize_phone(value, countries)
        if normalized:
            return normalized
        print('Please enter a valid phone number, including country code.')


def country_codes(value):
    """Validate a comma-separated list of ISO-style country codes."""
    codes = [item.strip().upper() for item in value.split(",") if item.strip()]
    return bool(codes) and all(re.match(r"^[A-Z]{2}$", code) for code in codes)


def parse_country_codes(value):
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def google_doc_url(value):
    return bool(DOC_URL_RE.match(value))


def drive_folder_url(value):
    return bool(FOLDER_URL_RE.match(value))


def application_root_path(value):
    """Validate an optional relative parent for application folders."""
    value = value.strip()
    if not value:
        return True
    from application_service.file_paths import normalize_file_path
    try:
        normalize_file_path(value)
    except ValueError:
        return False
    return True


def web_url(value):
    if not WEB_URL_RE.match(value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def create_applicant_profile(resume_path, profile_path, calendly_url=None, target_countries=None, personal=None):
    """Create a safe, provider-neutral profile scaffold from a supplied resume.

    The resume remains the source of truth. Discovery derives search terms from
    it automatically before querying job adapters.
    """
    expanded_resume_path = os.path.abspath(os.path.expanduser(resume_path))
    profile = {
        "schema_version": 1,
        "updated_on": date.today().isoformat(),
        "source_resume": {
            "path": expanded_resume_path,
            "file_name": os.path.basename(expanded_resume_path),
        },
        "personal": dict(personal or {}),
        "professional": {},
        "skills": {},
        "employment": [],
        "education": [],
        "certifications": [],
        "languages_listed_in_resume": [],
        "application_defaults": {
            **({"calendly_url": calendly_url} if calendly_url else {}),
        },
        "job_search": {
            "countries": [code.upper() for code in (target_countries or [])],
            "search_terms": [],
            "search_terms_source": "derived from source_resume by the discovery agent",
        },
        "unprovided_answers": {},
        "sources": {"personal.phone": "confirmed during onboarding"} if (personal or {}).get('phone') else {},
        "usage_notes": [
            "Populate structured fields from the supplied resume before submitting applications.",
            "Keep application-specific posting, status, and artifact references in the application journal.",
        ],
    }
    os.makedirs(os.path.dirname(profile_path), exist_ok=True)
    with open(profile_path, "w") as handle:
        json.dump(profile, handle, indent=2)
        handle.write("\n")
    return profile


def runtime_profile_path(profile_path):
    """Return a profile path that remains valid when the project is mounted."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    absolute = os.path.abspath(profile_path)
    try:
        if os.path.commonpath([project_root, absolute]) == project_root:
            return os.path.relpath(absolute, project_root)
    except ValueError:
        # Different drives on Windows cannot be represented by a relative path.
        pass
    return absolute


def sync_profile_to_drive(profile, local_profile_path=None):
    """Create the root profile through the persistence implementation when authenticated."""
    refresh_names = ("GOOGLE_REFRESH_TOKEN", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
    refresh_values = [os.environ.get(name) for name in refresh_names]
    if any(refresh_values) and not all(refresh_values):
        raise RuntimeError(
            "Google refresh authentication requires " + ", ".join(refresh_names)
        )
    refresh_configured = all(refresh_values)
    if not os.environ.get("GOOGLE_ACCESS_TOKEN") and not refresh_configured:
        return "authentication not configured"
    from application_service.google_docs_repository import repository_from_environment

    repository = repository_from_environment()
    # Validate the journal schema without parsing every application before
    # onboarding reports a Drive sync.
    if hasattr(repository, "journal_status"):
        status = repository.journal_status()
        if not status.get("valid"):
            missing = ", ".join(status.get("missing_states") or [])
            duplicates = ", ".join(status.get("duplicate_states") or [])
            details = []
            if missing:
                details.append("missing: " + missing)
            if duplicates:
                details.append("duplicates: " + duplicates)
            raise ValueError("journal state-tab validation failed ({}): {}".format(
                "; ".join(details), status.get("required_states", [])
            ))
    existing = repository.get_profile()
    if existing is not None:
        confirmed_phone = (profile.get('personal') or {}).get('phone')
        if confirmed_phone:
            existing.setdefault('personal', {})['phone'] = confirmed_phone
            existing.setdefault('sources', {})['personal.phone'] = 'confirmed during onboarding'
            repository.save_profile(existing)
        if local_profile_path:
            with open(local_profile_path, "w") as handle:
                json.dump(existing, handle, indent=2)
                handle.write("\n")
        return ("existing Drive profile retained, confirmed phone saved, and copied locally"
                if confirmed_phone else "existing Drive profile retained and copied locally")
    repository.save_profile(profile)
    return "created Drive profile"


def ensure_bookmark_settings(config):
    """Fill only missing bookmark-discovered keys. Keep a value the user already set."""
    section = config.get("bookmark-discovered")
    if not isinstance(section, dict):
        config["bookmark-discovered"] = dict(BOOKMARK_DISCOVERED)
        return True
    changed = False
    for key, value in BOOKMARK_DISCOVERED.items():
        if not str(section.get(key) or "").strip():
            section[key] = value
            changed = True
    return changed


def obtain_email_token(email=None):
    """Save a gmail.readonly refresh token when the desktop OAuth client is present."""
    from provisioning.gmail_login import ensure_gmail_login
    from provisioning.state import ProvisioningFailure
    try:
        created = ensure_gmail_login(email=email)
    except ProvisioningFailure as exc:
        print(str(exc))
        return False
    print('Saved the gmail.readonly refresh token.' if created else 'Gmail read-only login already saved.')
    return True


def save_runtime(path, config):
    temporary = str(path) + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Configure Drive, upload a résumé and extract an applicant profile")
    parser.add_argument('--applicant', action='store_true', help='Run Drive/applicant stages for an existing installation')
    args = parser.parse_args(argv or [])
    path = os.path.abspath(CONFIG_PATH)
    if os.path.exists(path):
        with open(path) as handle:
            config = json.load(handle)
        if args.applicant:
            from .drive_applicant import connect, prepare_applicant
            drive = config['google_drive']
            configured = drive.get('applicant_profile_path') or PROFILE_PATH
            profile_path = os.path.expanduser(configured)
            if not os.path.isabs(profile_path):
                profile_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), profile_path)
            try:
                storage = connect(drive['artifact_root_url'])
                previous = {}
                if os.path.isfile(profile_path):
                    with open(profile_path) as handle:
                        previous = json.load(handle)
                prepare_applicant(storage, profile_path,
                    countries=(previous.get('job_search') or {}).get('countries'),
                    calendly_url=(config.get('scheduling') or {}).get('calendly_url'))
                drive['application_root_path'] = 'Applications'
                drive['applicant_directory'] = 'Applicant'
                drive['applicant_profile_path'] = runtime_profile_path(profile_path)
                save_runtime(path, config)
                return 0
            except (KeyboardInterrupt, EOFError):
                return 130
            except (OSError, ValueError, RuntimeError) as exc:
                print('Applicant onboarding stopped: {}'.format(exc), file=sys.stderr)
                return 1
        configured_profile = config.get('google_drive', {}).get('applicant_profile_path')
        if configured_profile:
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            profile_path = os.path.expanduser(configured_profile)
            if not os.path.isabs(profile_path):
                profile_path = os.path.join(project_root, profile_path)
            with open(profile_path) as handle:
                profile = json.load(handle)
            countries = (profile.get('job_search') or {}).get('countries') or []
            if not normalize_phone((profile.get('personal') or {}).get('phone'), countries):
                print('Your existing setup is missing a confirmed phone number.')
                resume_path = (profile.get('source_resume') or {}).get('path')
                contacts = extract_resume_contacts(resume_path, countries) if resume_path and existing_path(resume_path) else {}
                profile.setdefault('personal', {})['phone'] = confirm_phone(contacts.get('phone'), countries)
                profile.setdefault('sources', {})['personal.phone'] = 'confirmed during onboarding'
                profile['updated_on'] = date.today().isoformat()
                print('Drive profile sync: {}'.format(sync_profile_to_drive(profile, profile_path)))
                # With no authentication, sync leaves the local profile to us.
                # With authentication, it may preserve a richer Drive profile.
                if not (os.environ.get('GOOGLE_ACCESS_TOKEN') or os.environ.get('GOOGLE_REFRESH_TOKEN')):
                    with open(profile_path, 'w') as handle:
                        json.dump(profile, handle, indent=2)
                        handle.write('\n')
                if ensure_bookmark_settings(config):
                    save_runtime(path, config)
                    print("Added bookmark-discovered model and effort to {}".format(path))
                obtain_email_token((profile.get('personal') or {}).get('email'))
                return 0
        if ensure_bookmark_settings(config):
            save_runtime(path, config)
            print("Added bookmark-discovered model and effort to {}".format(path))
        print("Onboarding is already complete: {}".format(path))
        obtain_email_token()
        return 0

    print("Job-search automation onboarding")
    print("No local runtime configuration was found.")
    connection = ask("Google Drive connection/profile name: ")
    journal_url = ask("Interview Journal Google Doc URL: ", google_doc_url)
    countries = parse_country_codes(ask("Target countries (ISO codes, comma-separated; e.g. IL,US): ", country_codes))
    calendly_url = ask("Calendly scheduling URL: ", web_url)
    folder = ask("Existing Google Drive folder URL or My Drive folder path: ", lambda value: bool(value))
    from .drive_applicant import connect, prepare_applicant
    from provisioning.state import ProvisioningFailure
    try:
        storage = connect(folder)
        profile_path = os.path.abspath(PROFILE_PATH)
        profile = prepare_applicant(storage, profile_path, countries, calendly_url)
        config = {
            "google_drive": {
                "connection": connection,
                "interview_journal_url": journal_url,
                "artifact_root_url": "https://drive.google.com/drive/folders/" + storage.owner.root_folder_id,
                "applicant_profile_path": runtime_profile_path(profile_path),
                "application_root_path": "Applications",
                "applicant_directory": "Applicant",
            },
            "scheduling": {"calendly_url": calendly_url},
            "bookmark-discovered": dict(BOOKMARK_DISCOVERED),
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = path + '.tmp'
        with open(temporary, 'w') as handle:
            json.dump(config, handle, indent=2)
            handle.write('\n')
        os.replace(temporary, path)
    except (KeyboardInterrupt, EOFError):
        print("Onboarding cancelled; runtime configuration was not created.")
        return 130
    except (OSError, ValueError, RuntimeError, ProvisioningFailure) as exc:
        print("Onboarding stopped: {}".format(exc), file=sys.stderr)
        return 1
    print("Saved runtime configuration to {}".format(path))
    print("Saved extracted profile to Applicant/applicant_profile.json in Drive.")
    obtain_email_token((profile.get('personal') or {}).get('email'))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
