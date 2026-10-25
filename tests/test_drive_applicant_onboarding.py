import base64
import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from reportlab.pdfgen.canvas import Canvas

from services.onboarding.drive_applicant import prepare_applicant, wait_for_resume


def resume_pdf():
    out = BytesIO()
    canvas = Canvas(out)
    canvas.drawString(40, 750, 'Example Candidate - backend engineer')
    canvas.drawString(40, 730, 'example@example.test - +972 50-234-5678')
    canvas.save()
    return out.getvalue()


class Storage:
    def __init__(self, uploads=None):
        self.uploads = iter(uploads or [[{'id': 'resume-id', 'name': 'resume.pdf'}]])
        self.last = []
        self.saved = []
        self.existing = None
        self.owner = SimpleNamespace(root_folder_id='chosen-root')
    def prepare(self):
        return {'Applicant': {'id': 'applicant'}, 'Applications': {'id': 'applications'}}
    def resumes(self):
        self.last = next(self.uploads, self.last)
        return self.last
    def read_resume(self, file):
        return resume_pdf()
    def profile(self):
        return self.existing
    def save_profile(self, profile):
        self.saved.append(profile)


def provider(prompt, context):
    assert list(context) == ['resume_text']
    assert 'backend engineer' in context['resume_text']
    return {'schema_version': 1, 'personal': {'name': 'Example Candidate'},
            'professional': {'headline': 'Backend engineer'}, 'employment': [],
            'skills': {'technical': ['backend']}, 'education': [], 'certifications': [],
            'languages_listed_in_resume': []}


def test_upload_gate_blocks_before_extraction_and_rescans(monkeypatch, tmp_path):
    storage = Storage([[], [], [{'id': 'resume-id', 'name': 'resume.pdf'}]])
    prompts = []
    def uploaded(prompt):
        assert not storage.saved
        assert not (tmp_path / 'profile.json').exists()
        prompts.append(prompt)
        return ''
    monkeypatch.setattr('builtins.input', lambda prompt: '')  # Confirm extracted phone.
    result = prepare_applicant(storage, tmp_path / 'profile.json', ['IL'], provider=provider, input_fn=uploaded)
    assert len(prompts) == 2
    assert result['source_resume']['path'] == 'Applicant/resume.pdf'
    assert result['source_resume']['drive_file_id'] == 'resume-id'
    assert result['source_resume']['sha256']
    assert storage.saved == [result]
    assert json.loads((tmp_path / 'profile.json').read_text()) == result


def test_cancel_missing_resume_never_creates_profile(tmp_path):
    storage = Storage([[]])
    def cancel(prompt):
        raise EOFError
    with pytest.raises(EOFError):
        prepare_applicant(storage, tmp_path / 'profile.json', provider=provider, input_fn=cancel)
    assert not storage.saved
    assert not (tmp_path / 'profile.json').exists()


def test_invalid_pdf_cannot_pass_upload_gate():
    storage = Storage()
    storage.read_resume = lambda file: b'not a PDF'
    def cancel(prompt):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        wait_for_resume(storage, cancel)
    assert not storage.saved


def test_failed_extraction_does_not_save_or_complete(tmp_path):
    storage = Storage()
    with pytest.raises(ValueError):
        prepare_applicant(storage, tmp_path / 'profile.json', provider=lambda p, c: {'schema_version': 1})
    assert not storage.saved
    assert not (tmp_path / 'profile.json').exists()


def test_same_resume_reuses_profile_without_model_or_phone_prompt(tmp_path, monkeypatch):
    content = resume_pdf()
    storage = Storage()
    storage.read_resume = lambda file: content
    storage.existing = {'schema_version': 1, 'personal': {'phone': '+972502345678'},
        'professional': {}, 'employment': [{'company': 'Retain me'}],
        'source_resume': {'path': 'Applicant/resume.pdf', 'sha256': hashlib.sha256(content).hexdigest()}}
    def unexpected(*args):
        pytest.fail('same résumé must reuse the profile')
    monkeypatch.setattr('builtins.input', unexpected)
    profile = prepare_applicant(storage, tmp_path / 'profile.json', ['IL'], provider=unexpected)
    assert profile['employment'] == [{'company': 'Retain me'}]


def test_multiple_resumes_require_explicit_selection():
    storage = Storage([[{'id': 'a', 'name': 'a.pdf'}, {'id': 'b', 'name': 'b.pdf'}]])
    answers = iter(['', '3', '2'])
    selected, content, text = wait_for_resume(storage, lambda prompt: next(answers))
    assert selected['id'] == 'b'


def test_portable_resume_reads_through_service_and_checks_hash():
    from services.source_resume import read_source_resume
    content = resume_pdf()
    calls = []
    class Client:
        def read_file(self, path):
            calls.append(path)
            return {'content_base64': base64.b64encode(content).decode()}
    profile = {'source_resume': {'path': 'Applicant/resume.pdf', 'sha256': hashlib.sha256(content).hexdigest()}}
    assert read_source_resume(Client(), profile) == content
    assert calls == ['Applicant/resume.pdf']
    profile['source_resume']['sha256'] = 'changed'
    with pytest.raises(ValueError, match='Resume changed'):
        read_source_resume(Client(), profile)


def test_stage_follows_drive_before_deployment():
    from provisioning.execute import up_steps
    names = [name for name, action in up_steps(None)]
    assert names.index('prepare_applicant') == names.index('provision_drive') + 1
    assert names.index('deploy_application') > names.index('prepare_applicant')


def test_service_boundary_reuses_folders_and_updates_profile_as_service_account():
    from application_service.applicant_onboarding import ApplicantStorage
    from application_service.google_drive_artifacts import FOLDER_MIME
    files = {'root': []}
    contents = {}
    calls = []
    class Store:
        root_folder_id = 'root'
        def __init__(self, identity): self.identity = identity
        def _forget_metadata(self): pass
        def _list_children(self, parent): return files.get(parent, [])
        def _ensure_folder(self, parent, name):
            assert self.identity == 'owner'
            item = {'id': name, 'name': name, 'mimeType': FOLDER_MIME}
            files[parent].append(item)
            files[name] = []
            calls.append(('folder', name))
            return item
        def _save_in_folder(self, parent, name, content, mime):
            assert self.identity == 'owner'
            item = {'id': 'profile', 'name': name}
            files[parent].append(item)
            contents['profile'] = content
            calls.append(('create', self.identity))
            return item
        def _replace_content(self, id, content, mime):
            calls.append(('update', self.identity))
            contents[id] = content
        def read(self, id): return contents[id]
    storage = ApplicantStorage(Store('owner'), Store('service'))
    storage.prepare()
    storage.prepare()
    profile = {'schema_version': 1, 'personal': {}, 'professional': {}, 'employment': []}
    storage.save_profile(profile)
    storage.save_profile(profile)
    assert calls.count(('folder', 'Applications')) == 1
    assert calls.count(('folder', 'Applicant')) == 1
    assert calls.count(('create', 'owner')) == 1
    assert calls.count(('update', 'service')) == 2
    assert storage.profile() == profile


def test_ambiguous_applicant_folder_is_rejected():
    from application_service.applicant_onboarding import ApplicantStorage
    from application_service.google_drive_artifacts import FOLDER_MIME
    class Store:
        root_folder_id = 'root'
        def _forget_metadata(self): pass
        def _list_children(self, parent):
            return [{'id': 'a', 'name': 'Applications', 'mimeType': FOLDER_MIME},
                    {'id': 'b', 'name': 'Applicant', 'mimeType': FOLDER_MIME},
                    {'id': 'c', 'name': 'Applicant', 'mimeType': FOLDER_MIME}]
    with pytest.raises(ValueError, match='Multiple Applicant'):
        ApplicantStorage(Store(), Store()).prepare()


def test_service_profile_verification_failure_never_writes_local_cache(tmp_path, monkeypatch):
    storage = Storage()
    def failed(profile):
        raise ValueError('Drive profile verification failed')
    storage.save_profile = failed
    monkeypatch.setattr('builtins.input', lambda prompt: '')
    with pytest.raises(ValueError, match='verification'):
        prepare_applicant(storage, tmp_path / 'profile.json', ['IL'], provider=provider)
    assert not (tmp_path / 'profile.json').exists()
