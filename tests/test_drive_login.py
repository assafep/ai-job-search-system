import json

import pytest

from application_service.user_drive import completion_store, file_credentials
from provisioning.drive_login import client_credentials
from provisioning.state import ProvisioningFailure


def test_desktop_client_file_is_read(tmp_path):
    path = tmp_path / 'oauth-client.json'
    path.write_text(json.dumps({'installed': {'client_id': 'id', 'client_secret': 'secret'}}))
    assert client_credentials(path) == ('id', 'secret')


def test_saved_login_is_recognized(tmp_path):
    path = tmp_path / 'drive-user.json'
    path.write_text(json.dumps({'client_id': 'id', 'client_secret': 'secret', 'refresh_token': 'refresh'}))
    from provisioning.drive_login import token_saved
    assert token_saved(path)
    assert not token_saved(tmp_path / 'missing.json')


def test_missing_client_explains_where_to_create_one(tmp_path):
    with pytest.raises(ProvisioningFailure) as error:
        client_credentials(tmp_path / 'missing.json', email='ada@example.com', project_id='job-search-project')
    text = str(error.value)
    assert 'ada@example.com' in text
    assert 'job-search-project' in text
    assert 'Desktop' in text
    assert 'https://console.cloud.google.com/apis/credentials?project=job-search-project' in text
    assert 'Select project job-search-project;' in text
    assert 'matches the Google Cloud project ID printed earlier in the log' in text
    assert 'Create credentials > OAuth client ID' in text
    assert 'Recommended name: AJSS Application Service.' in text
    assert 'Mark the checkbox "Use this client for an AI-powered agent"' in text
    assert 'client ID and client secret' in text
    assert '.onboarding/oauth-client.json' in text
    assert './deployment/ctl.sh up' in text
    assert 'assafep' not in text


def test_instructions_require_the_applicant_email():
    with pytest.raises(ProvisioningFailure):
        from provisioning.drive_login import file_login_instructions
        file_login_instructions('')


def test_completion_uses_the_service_account_until_the_user_logs_in():
    sentinel = object()
    assert file_credentials({}) is None
    assert completion_store(sentinel, {}) is sentinel


def test_partial_drive_file_login_is_rejected():
    with pytest.raises(RuntimeError):
        file_credentials({'GOOGLE_DRIVE_FILE_CLIENT_ID': 'id'})


def test_existing_client_goes_directly_to_consent(monkeypatch, capsys):
    from provisioning import drive_login
    saved = []
    monkeypatch.setattr(drive_login, 'token_saved', lambda: False)
    monkeypatch.setattr(drive_login, 'client_credentials', lambda **kwargs: ('id', 'secret'))
    monkeypatch.setattr(drive_login, 'browser_credentials', lambda *args: {'grant': 'saved'})
    monkeypatch.setattr(drive_login, 'save_user_token', saved.append)
    assert drive_login.ensure_file_login(email='ada@example.com')
    output = capsys.readouterr().out
    assert 'OAuth client JSON found' in output
    assert 'Create credentials' not in output
    assert 'rerun' not in output
    assert saved == [{'grant': 'saved'}]


def test_missing_client_distinguishes_setup_from_consent():
    from provisioning.drive_login import file_login_instructions
    output = file_login_instructions('ada@example.com', 'project')
    assert 'Expected file:' in output
    assert 'No consent browser will open until' in output
    assert 'https://www.googleapis.com/auth/drive.file' not in output


def test_saved_grant_skips_consent(monkeypatch):
    from provisioning import drive_login
    monkeypatch.setattr(drive_login, 'token_saved', lambda: True)
    monkeypatch.setattr(drive_login, 'client_credentials', lambda **kwargs: pytest.fail('unexpected client lookup'))
    assert drive_login.ensure_file_login(email='ada@example.com') is False
