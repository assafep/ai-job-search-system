"""Real SDK/HTTP tests against isolated memory state, never external adapters."""
import asyncio
import json
import threading
from http.server import HTTPServer

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from application_service.mcp_adapter import Catalog, create_server


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('REPOSITORY_BACKEND', 'memory')
    monkeypatch.setenv('APPLICATION_SERVICE_TOKEN', 'isolated-mcp-test-token')
    monkeypatch.setenv('AJSS_MCP_ISSUER', 'https://ajss.example.test')
    from application_service import main
    from application_service.repository import MemoryRepository
    monkeypatch.setattr(main, 'repository', MemoryRepository())
    server = HTTPServer(('127.0.0.1', 0), main.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield 'http://127.0.0.1:' + str(server.server_port), main.repository
    server.shutdown()
    server.server_close()
    worker.join(timeout=3)


def test_sdk_handshake_discovery_mutations_and_retry(api):
    url, repository = api
    async def run():
        async with streamablehttp_client(url + '/mcp', headers={
                'Authorization': 'Bearer isolated-mcp-test-token'}) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == 'ajss-application-service'
                listing = await session.list_tools()
                tools = {tool.name: tool for tool in listing.tools}
                assert tools['listApplications'].annotations.readOnlyHint
                assert tools['deleteApplication'].annotations.destructiveHint
                assert tools['createApplication'].annotations.idempotentHint
                assert (await session.call_tool('journalQueueCounts', {})).isError is False
                payload = {'body': {'company': 'Example', 'role': 'Engineer',
                                    'posting_url': 'https://jobs.example.test/1'}}
                invalid = await session.call_tool('createApplication', payload)
                assert invalid.isError
                assert repository.list_applications() == []
                payload['body']['idempotency_key'] = 'mcp-create-test-1'
                first = await session.call_tool('createApplication', payload)
                assert not first.isError
                second = await session.call_tool('createApplication', payload)
                assert first.structuredContent['data']['application_id'] == second.structuredContent['data']['application_id']
                assert len(repository.list_applications()) == 1
                application_id = first.structuredContent['data']['application_id']
                found = await session.call_tool('getApplication', {'application_id': application_id})
                assert found.structuredContent['data']['company'] == 'Example'
                missing = await session.call_tool('getApplication', {'application_id': 'missing'})
                assert missing.isError and missing.structuredContent['status'] == 404
                unknown = await session.call_tool('notATool', {})
                assert unknown.isError
    asyncio.run(run())


def test_http_auth_origin_protocol_and_malformed_requests(api):
    url, _ = api
    with httpx.Client() as client:
        response = client.post(url + '/mcp', json={})
        assert response.status_code == 401
        assert '/.well-known/oauth-protected-resource/mcp' in response.headers['www-authenticate']
        headers = {'Authorization': 'Bearer isolated-mcp-test-token',
                   'Accept': 'application/json, text/event-stream'}
        assert client.get(url + '/mcp', headers=headers).status_code == 405
        assert client.delete(url + '/mcp', headers=headers).status_code == 405
        assert client.post(url + '/mcp', headers=dict(headers, Origin='https://attacker.test'), json={}).status_code == 403
        malformed = client.post(url + '/mcp', headers=dict(headers, **{'Content-Type': 'application/json'}), content=b'not json')
        assert malformed.status_code == 400
        metadata = client.get(url + '/.well-known/oauth-protected-resource/mcp')
        assert metadata.status_code == 200
        assert metadata.json()['resource'] == 'https://ajss.example.test/mcp'
        auth = client.get(url + '/.well-known/oauth-authorization-server')
        assert auth.status_code == 200
        assert auth.json()['registration_endpoint'] == 'https://ajss.example.test/register'


def test_catalog_rejects_redirects_and_parameter_injection():
    catalog = Catalog()
    for identifier in ('../profile', 'x/transition', 'x?query', '%2fprofile'):
        with pytest.raises(ValueError):
            catalog.request('getApplication', {'application_id': identifier})
    with pytest.raises(ValueError):
        catalog.request('listApplications', {'url': 'https://attacker.test'})
    with pytest.raises(ValueError):
        catalog.request('listApplications', {'status': 'Imaginary Queue'})
    assert catalog.request('readRootRelativeFile', {'path': 'a/b ?c'})[1] == '/files?path=a%2Fb+%3Fc'


@pytest.mark.parametrize("error", [RuntimeError, ValueError])
def test_backend_failures_do_not_disclose_credentials(error):
    async def transport(method, path, payload):
        raise error('Bearer secret; https://private-database/password')
    async def run():
        from mcp import types
        server = create_server(transport)
        result = await server.request_handlers[types.CallToolRequest](types.CallToolRequest(
            method='tools/call', params=types.CallToolRequestParams(name='journalQueueCounts', arguments={})))
        serialized = result.model_dump_json()
        assert 'secret' not in serialized and 'private-database' not in serialized
        assert 'reconcile' in serialized
    asyncio.run(run())


def test_stdio_launcher_calls_the_api_over_verified_https(api, tmp_path):
    import datetime
    import ipaddress
    import os
    import ssl
    import sys
    from pathlib import Path
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client
    from application_service.main import Handler

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
                x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / 'test-cert.pem', tmp_path / 'test-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server = HTTPServer(('127.0.0.1', 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def run():
        launcher = Path(__file__).resolve().parents[1] / 'deployment' / 'ctl.sh'
        params = StdioServerParameters(command=str(launcher), args=['mcp-stdio'], env={
            'AJSS_INSIDE_PROVISION': '1',
            'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
            'APPLICATION_SERVICE_URL': 'https://localhost:' + str(server.server_port),
            'APPLICATION_SERVICE_TOKEN': 'isolated-mcp-test-token', 'SSL_CERT_FILE': str(cert_path)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert (await session.list_tools()).tools
                result = await session.call_tool('journalQueueCounts', {})
                assert not result.isError
                assert result.structuredContent['data']['Bookmarked'] == 0
    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
