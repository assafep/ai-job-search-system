"""Guided Drive setup and a mandatory, resumable résumé-upload gate."""
from copy import deepcopy
import hashlib
import json
from datetime import date
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from application_service.profile import validate_profile
from .resume_contacts import extract_contacts


def connect(folder):
    from provisioning.drive_storage import bind
    print('Stage 1: Google Drive service-account access', flush=True)
    print('Choose an existing folder you own. Google login will open if needed.')
    print('The setup creates/reuses a service account and grants it Editor access only to that folder.')
    result = bind(folder)
    print('Service account: ' + result['service_account'])
    print('Read/write access verified for https://drive.google.com/drive/folders/' + result['folder_id'])
    return storage_from_checkpoint(result)


def storage_from_checkpoint(record=None):
    from provisioning.drive_storage import REPO, ensure_drive_login
    from application_service.google_auth import ServiceAccountToken
    from application_service.google_drive_artifacts import GoogleDriveArtifactStore
    from application_service.applicant_onboarding import ApplicantStorage
    if record is None:
        record = json.loads((REPO / '.onboarding' / 'drive-folder.json').read_text())
    key = json.loads((REPO / '.onboarding' / 'drive-service-account.json').read_text())
    root = 'https://drive.google.com/drive/folders/' + record['folder_id']
    return ApplicantStorage(GoogleDriveArtifactStore(ensure_drive_login(), root),
                            GoogleDriveArtifactStore(ServiceAccountToken(key), root))


def wait_for_resume(storage, input_fn=None):
    input_fn = input_fn or input
    print('Stage 2: Applications and Applicant folders', flush=True)
    folders = storage.prepare()
    url = 'https://drive.google.com/drive/folders/' + folders['Applicant']['id']
    print('Place your original résumé PDF in Applicant: ' + url, flush=True)
    print('Onboarding cannot continue until a readable résumé is present. Ctrl+C cancels safely.')
    while True:
        candidates = storage.resumes()
        if not candidates:
            input_fn('Upload the résumé to Applicant, then press Enter to check again: ')
            continue
        if len(candidates) > 1:
            print('Choose the résumé to use:')
            for index, file in enumerate(candidates, 1):
                print('  {}. {}'.format(index, file['name']))
            choice = input_fn('Résumé number: ').strip()
            if not choice.isdigit() or not 1 <= int(choice) <= len(candidates):
                print('Choose one of the displayed résumés.')
                continue
            selected = candidates[int(choice) - 1]
        else:
            selected = candidates[0]
        if '/' in selected['name'] or chr(92) in selected['name'] or any(
                f['id'] != selected['id'] and f['name'] == selected['name'] for f in candidates):
            print('Give the selected résumé a unique filename without slashes in Applicant.')
            input_fn('After renaming the file, press Enter to check again: ')
            continue
        content = storage.read_resume(selected)
        try:
            reader = PdfReader(BytesIO(content))
            text = '\n'.join(page.extract_text() or '' for page in reader.pages).strip()
            if not text:
                raise ValueError('PDF contains no extractable text')
        except Exception as exc:
            print('The résumé is not a readable text PDF ({}). Replace it in Applicant.'.format(type(exc).__name__))
            input_fn('After replacing the PDF, press Enter to check again: ')
            continue
        return selected, content, text


def extract_profile(text, provider=None):
    """Stateless extraction. Storage clients and Google credentials stay outside."""
    from services.agent_runner import run
    from services.worker_main import load_provider
    return run('applicant-profile', {'resume_text': text}, provider or load_provider())


def prepare_applicant(storage, profile_path, countries=None, calendly_url=None, provider=None, input_fn=None):
    from .main import confirm_phone
    from .resume_contacts import normalize_phone
    selected, content, text = wait_for_resume(storage, input_fn)
    digest = hashlib.sha256(content).hexdigest()
    existing = storage.profile()
    reused = bool(existing and (existing.get('source_resume') or {}).get('sha256') == digest)
    if reused:
        profile = existing
        print('Reusing the extracted profile for this exact résumé.')
    else:
        print('Stage 3: Extract applicant_profile.json from the résumé', flush=True)
        profile = extract_profile(text, provider)
        validate_profile(profile)
        if existing:
            for key in ('job_search', 'application_defaults'):
                if isinstance(existing.get(key), dict):
                    profile[key] = deepcopy(existing[key])
        contacts = extract_contacts(text, countries)
        profile.setdefault('personal', {}).update(contacts)
    profile['source_resume'] = {
        'path': 'Applicant/' + selected['name'], 'file_name': selected['name'],
        'drive_file_id': selected['id'], 'sha256': digest,
    }
    profile['updated_on'] = date.today().isoformat()
    profile.setdefault('job_search', {})
    if countries is not None:
        profile['job_search']['countries'] = countries
    if calendly_url:
        profile.setdefault('application_defaults', {})['calendly_url'] = calendly_url
    if not reused or not normalize_phone(profile['personal'].get('phone'), countries):
        profile['personal']['phone'] = confirm_phone(profile['personal'].get('phone'), countries)
    profile.setdefault('sources', {})['personal.phone'] = 'confirmed during onboarding'
    storage.save_profile(profile)
    # Local compatibility cache is written only after the canonical Drive write verifies.
    path = Path(profile_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)
    return profile
