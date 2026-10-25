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
