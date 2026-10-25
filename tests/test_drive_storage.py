from provisioning.drive_storage import folder_query, permission_action, project_id_for, resolve_folder, split_drive_path
from provisioning.state import ProvisioningFailure


def test_path_ignores_my_drive_prefix():
    assert split_drive_path('My Drive/JobSearch/JobSearch2026') == ('JobSearch', 'JobSearch2026')


def test_project_id_is_stable():
    assert project_id_for('folder') == project_id_for('folder')
    assert project_id_for('folder').startswith('ajss-drive-')


def test_folder_query_escapes_quotes():
    assert "name = 'O\\'Brien'" in folder_query("O'Brien", 'root')


def test_permission_keeps_writer_and_upgrades_reader():
    email = 'drive-writer@ajss.iam.gserviceaccount.com'
    assert permission_action([{'emailAddress': email, 'role': 'writer', 'id': '1'}], email) == ('keep', '1')
    assert permission_action([{'emailAddress': email, 'role': 'reader', 'id': '2'}], email) == ('upgrade', '2')
    assert permission_action([], email) == ('create', None)


class Listing:
    def __init__(self):
        self.calls = []

    def request(self, method, url, body=None):
        self.calls.append(url)
        if 'JobSearch2026' in url or 'JobSearch2026' in (url):
            return {'files': [{'id': 'child', 'name': 'JobSearch2026'}]}
        return {'files': [{'id': 'parent', 'name': 'JobSearch'}]}


def test_resolve_walks_existing_folders():
    api = Listing()
    folder = resolve_folder(api, 'My Drive/JobSearch/JobSearch2026')
    assert folder['id'] == 'child'
    assert len(api.calls) == 2


def test_missing_folder_stops():
    class Empty:
        def request(self, method, url, body=None):
            return {'files': []}

    try:
        resolve_folder(Empty(), 'My Drive/JobSearch')
    except ProvisioningFailure as exc:
        assert 'does not exist' in str(exc)
        assert 'Folders here' in str(exc)
    else:
        raise AssertionError('missing folder was accepted')


def test_my_drive_write_probe_is_created_as_owner_then_verified_as_service_account(monkeypatch):
    from provisioning import drive_storage
    calls = []
    class Owner:
        def request(self, method, url, body=None):
            calls.append(('owner', method, body))
            return {'id': 'probe'}
    class Service:
        def __init__(self, token, opener): pass
        def request(self, method, url, body=None):
            calls.append(('service', method, body))
            return {'id': 'probe'}
    monkeypatch.setattr(drive_storage, 'service_account_token', lambda key, opener: 'token')
    monkeypatch.setattr(drive_storage, 'Api', Service)
    assert drive_storage.verify_writer({}, 'chosen-root', owner_api=Owner()) == 'probe'
    assert [item[:2] for item in calls] == [('owner', 'POST'), ('service', 'GET'), ('service', 'PATCH'), ('service', 'PATCH')]
    assert calls[0][2]['parents'] == ['chosen-root']
    assert calls[-1][2] == {'trashed': True}
