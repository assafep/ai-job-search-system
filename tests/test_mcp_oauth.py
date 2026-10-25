import asyncio
import base64
import copy
import hashlib
import json
import threading
import time
from contextlib import contextmanager
from http.server import HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from application_service import mcp_oauth
from application_service.mcp_oauth import Provider, Store


@pytest.fixture
def oauth_api(monkeypatch):
    """Keep the production OAuth store, substituting only its database connection."""
    rows = {}
    class Result:
        def __init__(self, row):
            self.row = row
        def fetchone(self):
            return self.row
    class Connection:
        def execute(self, sql, params):
            key = params[0]
            if sql.startswith('SELECT data'):
                row = rows.get(key)
                return Result((copy.deepcopy(row),) if row else None)
            if sql.startswith('DELETE'):
                row = rows.pop(key, None)
                return Result((row,) if row else None)
            raise AssertionError(sql)
    @contextmanager
    def connect():
        yield Connection()
    def save(connection, key, data):
        rows[key] = copy.deepcopy(data)
    from application_service import database, initialization
    monkeypatch.setattr(database, 'connect', connect)
    monkeypatch.setattr(initialization, 'save_resource', save)
    monkeypatch.setenv('REPOSITORY_BACKEND', 'memory')
    monkeypatch.setenv('APPLICATION_SERVICE_TOKEN', 'owner-test-token')
    monkeypatch.setenv('AJSS_MCP_ISSUER', 'https://ajss.example.test')
    from application_service import main
    from application_service.repository import MemoryRepository
    monkeypatch.setattr(main, 'repository', MemoryRepository())
    server = HTTPServer(('127.0.0.1', 0), main.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield 'http://127.0.0.1:' + str(server.server_port), rows
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def authorize(client, base):
    redirect = 'https://client.example.test/callback'
    registered = client.post(base + '/register', json={
        'client_name': 'Test client', 'redirect_uris': [redirect],
        'token_endpoint_auth_method': 'none',
        'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code']})
    assert registered.status_code == 201, registered.text
    registration = registered.json()
    verifier = 'test-pkce-verifier-with-more-than-forty-three-characters'
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    params = {'response_type': 'code', 'client_id': registration['client_id'],
              'redirect_uri': redirect, 'code_challenge': challenge, 'code_challenge_method': 'S256',
              'scope': 'ajss', 'state': 'test-client-csrf-state', 'resource': 'https://ajss.example.test/mcp'}
    authorization = client.get(base + '/authorize', params=params)
    assert authorization.status_code in (302, 307), authorization.text
    location = urlsplit(authorization.headers['location'])
    consent = base + location.path + '?' + location.query
    page = client.get(consent)
    assert page.status_code == 200 and 'Test client' in page.text
    # no-referrer makes browsers send Origin: null on this form's POST.
    assert page.headers['referrer-policy'] == 'same-origin'
    assert 'owner-test-token' not in page.text
    assert client.post(consent, data={'token': 'wrong'}).status_code == 403
    assert client.post(consent, data={'token': 'owner-test-token'}, headers={'Origin': 'https://attacker.test'}).status_code == 403
    assert client.post(consent, data={'token': 'owner-test-token'}, headers={'Origin': 'null'}).status_code == 403
    approved = client.post(consent, data={'token': 'owner-test-token'}, headers={'Origin': 'https://ajss.example.test'})
    assert approved.status_code == 303, approved.text
    result = parse_qs(urlsplit(approved.headers['location']).query)
    assert result['state'] == ['test-client-csrf-state']
    assert 'owner-test-token' not in approved.headers['location']
    assert client.post(consent, data={'token': 'owner-test-token'}).status_code == 400
    return dict(grant_type='authorization_code', client_id=registration['client_id'],
                redirect_uri=redirect, code=result['code'][0], code_verifier=verifier)


def test_oauth_pkce_single_use_rotation_revocation_and_audience(oauth_api, monkeypatch):
    base, _ = oauth_api
    with httpx.Client(follow_redirects=False) as client:
        grant = authorize(client, base)
        invalid = client.post(base + '/token', data=dict(grant, code_verifier='wrong'))
        assert invalid.status_code == 400
        assert client.post(base + '/token', data=dict(grant, redirect_uri='https://attacker.test')).status_code == 400
        response = client.post(base + '/token', data=grant)
        assert response.status_code == 200, response.text
        tokens = response.json()
        assert tokens['access_token'] != 'owner-test-token'
        assert tokens['scope'] == 'ajss'
        assert client.post(base + '/token', data=grant).status_code == 400
        headers = {'Authorization': 'Bearer ' + tokens['access_token'],
                   'Accept': 'application/json, text/event-stream'}
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': 'journalQueueCounts', 'arguments': {}}}
        assert client.post(base + '/mcp', json=request, headers=headers).json()['result']['isError'] is False
        assert client.get(base + '/applications', headers=headers).status_code == 401
        refreshed = client.post(base + '/token', data={
            'grant_type': 'refresh_token', 'client_id': grant['client_id'],
            'refresh_token': tokens['refresh_token']})
        assert refreshed.status_code == 200, refreshed.text
        new = refreshed.json()
        assert new['refresh_token'] != tokens['refresh_token']
        assert client.post(base + '/mcp', json=request, headers=headers).status_code == 401
        assert client.post(base + '/token', data={
            'grant_type': 'refresh_token', 'client_id': grant['client_id'],
            'refresh_token': tokens['refresh_token']}).status_code == 400
        provider = Provider('https://other.example.test')
        assert asyncio.run(provider.load_access_token(new['access_token'])) is None
        headers['Authorization'] = 'Bearer ' + new['access_token']
        assert client.post(base + '/mcp', json=request, headers=headers).status_code == 200
        revoked = client.post(base + '/revoke', data={'client_id': grant['client_id'], 'token': new['refresh_token']})
        assert revoked.status_code == 200, revoked.text
        assert client.post(base + '/mcp', json=request, headers=headers).status_code == 401
        # Service credential rotation invalidates issued records, including clients.
        monkeypatch.setenv('APPLICATION_SERVICE_TOKEN', 'rotated-owner-token')
        assert asyncio.run(Provider('https://ajss.example.test').get_client(grant['client_id'])) is None


def test_expired_and_consumed_records_cannot_be_reused(oauth_api):
    store = Store()
    store.put('code', 'expired', {'expires_at': time.time() - 1})
    assert store.get('code', 'expired') is None
    store.put('code', 'once', {'expires_at': time.time() + 60})
    assert store.get('code', 'once', consume=True)
    assert store.get('code', 'once', consume=True) is None
