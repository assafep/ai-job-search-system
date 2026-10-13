from services.onboarding.resume_contacts import extract_contacts, normalize_phone
from services.onboarding.main import confirm_phone


def test_resume_phone_is_extracted_with_other_contact_data():
    personal = extract_contacts('Example Candidate\nexample@example.test\nPhone: +972 50-234-5678\nlinkedin.com/in/example', ['IL'])
    assert personal['phone'] == '+972502345678'
    assert personal['email'] == 'example@example.test'
    assert personal['name'] == 'Example Candidate'


def test_found_phone_is_offered_as_enter_default(monkeypatch):
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        return ''
    monkeypatch.setattr('builtins.input', answer)
    assert confirm_phone('+972 50-234-5678', ['IL']) == '+972502345678'
    assert '+972502345678' in prompts[0]


def test_missing_phone_requires_valid_answer(monkeypatch):
    answers = iter(['', 'unknown', '+972 50-234-5678'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    assert confirm_phone(None, ['IL']) == '+972502345678'


def test_user_can_correct_extracted_phone(monkeypatch):
    monkeypatch.setattr('builtins.input', lambda prompt: '+972 52-345-6789')
    assert confirm_phone('+972502345678', ['IL']) == '+972523456789'


def test_multiple_resume_numbers_have_no_arbitrary_default():
    assert 'phone' not in extract_contacts('Phone +972502345678\nReference +972523456789', ['IL'])
    assert 'phone' not in extract_contacts('2011–2012\n24 years experience', ['IL'])
    assert normalize_phone('not a phone', ['IL']) is None


def test_existing_onboarding_collects_missing_phone_without_resetting_profile(tmp_path, monkeypatch):
    import json
    from services.onboarding import main as onboarding
    resume = tmp_path / 'resume.txt'
    resume.write_text('Example Candidate\nPhone: +972 50-234-5678')
    profile_path = tmp_path / 'profile.json'
    profile_path.write_text(json.dumps({'personal': {'name': 'Example Candidate'},
        'source_resume': {'path': str(resume)}, 'job_search': {'countries': ['IL']},
        'employment': [{'company': 'Preserved company'}]}))
    runtime = tmp_path / 'runtime.json'
    runtime.write_text(json.dumps({'google_drive': {'applicant_profile_path': str(profile_path)}}))
    monkeypatch.setattr(onboarding, 'CONFIG_PATH', str(runtime))
    monkeypatch.delenv('GOOGLE_ACCESS_TOKEN', raising=False)
    monkeypatch.delenv('GOOGLE_REFRESH_TOKEN', raising=False)
    monkeypatch.delenv('GOOGLE_CLIENT_ID', raising=False)
    monkeypatch.delenv('GOOGLE_CLIENT_SECRET', raising=False)
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        return ''
    monkeypatch.setattr('builtins.input', answer)
    assert onboarding.main() == 0
    saved = json.loads(profile_path.read_text())
    assert saved['personal']['phone'] == '+972502345678'
    assert saved['employment'] == [{'company': 'Preserved company'}]
    assert len(prompts) == 1
