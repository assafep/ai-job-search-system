import json
import threading
from http.server import HTTPServer
from unittest.mock import Mock

import httpx
import pytest

from application_service import bulk_updates
from application_service.repository import MemoryRepository
from services.application_service_client import ApplicationServiceClient, BulkUpdateError
from services.bulk_updates import item, require_success


@pytest.fixture
def repository():
    return MemoryRepository()


def create(repository, index=1):
    return repository.create_application('Example ' + str(index), 'Engineer', 'https://example.test/' + str(index))


def test_validation_happens_before_any_item_is_written(repository):
    app = create(repository)
    valid = item('update_application', application=app, data={'role': 'Changed'})
    with pytest.raises(ValueError):
        bulk_updates.execute(repository, {'items': [valid, dict(valid, operation='arbitrary_url')]})
    assert app.role == 'Engineer' and not repository.files
    for items in [[], [valid] * 26, [valid, valid]]:
        with pytest.raises(ValueError): bulk_updates.execute(repository, {'items': items})
    with pytest.raises(ValueError):
        bulk_updates.execute(repository, {'items': [dict(valid, data={'role': 'x', 'url': 'https://elsewhere'})]})


def test_order_partial_failure_replay_and_changed_key_payload(repository):
    first, second = create(repository), create(repository, 2)
    updates = [item('update_application', application=first, data={'role': 'Updated'}),
               item('update_application', application={'application_id': 'missing', 'status': 'Bookmarked'}, data={'role': 'Missing'}),
               item('update_application', application=second, data={'company': 'Other'})]
    response = bulk_updates.execute(repository, {'items': updates})
    assert response['succeeded'] == 2 and response['failed'] == 1
    assert [r['index'] for r in response['results']] == [0, 1, 2]
    assert first.role == 'Updated' and second.company == 'Other'
    assert bulk_updates.execute(repository, {'items': updates}) == response
    changed = dict(updates[0], data={'role': 'Different'})
    result = bulk_updates.execute(repository, {'items': [changed]})
    assert result['results'][0]['error']['code'] == 'idempotency_conflict'
    assert first.role == 'Updated'
    with pytest.raises(BulkUpdateError): require_success(response)


def test_duplicate_removal_and_completion_replay_after_deletion(repository):
    duplicate, rejected = create(repository), create(repository, 2)
    duplicate.status, rejected.status = 'Duplicate', 'Rejected'
    updates = [item('remove_duplicate', application=duplicate),
               item('complete_application', application=rejected, data={'reason': 'rejected'})]
    first = bulk_updates.execute(repository, {'items': updates})
    assert first['failed'] == 0
    assert duplicate.application_id not in repository.applications
    assert repository.list_main_journal_applications(['Rejected']) == []
    assert bulk_updates.execute(repository, {'items': updates}) == first


def test_new_activity_prevents_stale_ghosting_completion(repository):
    app = create(repository)
    app.status = 'Applied'
    app.updated_at = '2026-10-01T00:00:00+00:00'
    update = item('complete_application', application=app,
                  data={'reason': 'ghosted', 'inactive_before': app.updated_at})
    repository.append_event(app.application_id, 'recent', {'bullets': ['New activity']})
    response = bulk_updates.execute(repository, {'items': [update]})
    assert response['results'][0]['error']['code'] == 'precondition_failed'
    assert app.status == 'Applied'


def test_lost_result_is_not_reexecuted(repository, monkeypatch):
    app = create(repository)
    update = item('update_application', application=app, data={'role': 'Updated'})
    calls = []
    def uncertain(repo, operation):
        calls.append(operation)
        app.role = 'Updated'
        raise TimeoutError('may contain credentials that must not be returned')
    monkeypatch.setattr(bulk_updates, 'apply', uncertain)
    first = bulk_updates.execute(repository, {'items': [update]})
    assert first['results'][0]['status'] == 'reconciliation_required'
    assert 'credentials' not in str(first)
    assert bulk_updates.execute(repository, {'items': [update]}) == first
    assert len(calls) == 1


def test_failure_to_save_result_leaves_a_blocked_intent(repository, monkeypatch):
    app = create(repository)
    update = item('update_application', application=app, data={'role': 'Updated'})
    store = repository.store_file
    calls = []
    def broken(path, content, *args):
        calls.append(path)
        if len(calls) == 2: raise ConnectionError('lost completion receipt')
        return store(path, content, *args)
    monkeypatch.setattr(repository, 'store_file', broken)
    first = bulk_updates.execute(repository, {'items': [update]})
    assert first['failed'] == 1 and app.role == 'Updated'
    retry = bulk_updates.execute(repository, {'items': [update]})
    assert retry['results'][0]['error']['code'] == 'uncertain_outcome'
    assert len(calls) == 2


def test_gmail_updates_do_not_store_credentials_or_ack_a_new_grant(repository, monkeypatch):
    from application_service import gmail_grant
    saved = {}
    monkeypatch.setattr(gmail_grant, 'load_record', lambda: dict(saved))
    monkeypatch.setattr(gmail_grant, 'save_record', lambda record: saved.update(record))
    def mutate(change):
        updated, result = change(dict(saved))
        if updated is not None: saved.update(updated)
        return result
    monkeypatch.setattr(gmail_grant, 'mutate_record', mutate)
    grant = {'client_id': 'test-client', 'client_secret': 'PRIVATE-CLIENT-SECRET', 'refresh_token': 'PRIVATE-REFRESH-TOKEN'}
    response = bulk_updates.execute(repository, {'items': [item('install_gmail_grant', data=grant)]})
    assert response['failed'] == 0 and saved['refresh_token'] == grant['refresh_token']
    receipts = str(repository.files)
    assert 'PRIVATE-CLIENT-SECRET' not in receipts and 'PRIVATE-REFRESH-TOKEN' not in receipts
    old_expiry = saved['refresh_expires_at']
    saved['refresh_expires_at'] += 100
    response = bulk_updates.execute(repository, {'items': [item('acknowledge_gmail_notice', data={'refresh_expires_at': old_expiry})]})
    assert response['failed'] == 1 and 'notice_for' not in saved


def test_http_contract_auth_bulk_client_chunking_and_mcp_catalog(repository, monkeypatch):
    from application_service import main
    from application_service.mcp_adapter import Catalog
    monkeypatch.setattr(main, 'repository', repository)
    monkeypatch.setenv('APPLICATION_SERVICE_TOKEN', 'test-bulk-token')
    server = HTTPServer(('127.0.0.1', 0), main.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = 'http://127.0.0.1:' + str(server.server_port)
    try:
        assert httpx.post(url + '/bulk-updates', json={'items': []}).status_code == 401
        client = ApplicationServiceClient(url, auth_token='test-bulk-token')
        updates = [item('update_application', application=create(repository, n), data={'role': 'Updated'}) for n in range(30)]
        response = client.bulk_update_items(updates)
        assert response['succeeded'] == 30 and response['failed'] == 0
        assert [r['index'] for r in response['results']] == list(range(30))
        assert client.bulk_update_items(updates) == response
        assert client.bulk_update_items([]) == {'results': [], 'succeeded': 0, 'failed': 0}
        method, path, body = Catalog().request('bulkUpdateItems', {'body': {'items': updates[:1]}})
        assert (method, path) == ('POST', '/bulk-updates')
        assert body['items'] == updates[:1]
        with pytest.raises(ValueError): Catalog().request('bulkUpdateItems', {'body': {'items': [dict(updates[0], idempotency_key='')]}})
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_transition_and_event_operations_preserve_order_and_deduplicate(repository):
    app = create(repository)
    transition = item('transition_application', application=app, data={'target': 'Prepared'})
    event = item('append_event', application={'application_id': app.application_id, 'status': 'Prepared'},
                 data={'event_type': 'bulk_note', 'data': {'bullets': ['Explicit bulk update']}})
    response = bulk_updates.execute(repository, {'items': [transition, event]})
    assert response['failed'] == 0 and app.status == 'Prepared'
    assert bulk_updates.execute(repository, {'items': [transition, event]}) == response
    assert sum(e.event_type == 'bulk_note' for e in repository.list_events(app.application_id)) == 1
