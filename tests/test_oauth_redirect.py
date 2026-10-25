import json

from provisioning.oauth_redirect import CALLBACK, LOOPBACK, ensure_callback


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def test_onboarding_registers_the_callback_on_a_web_client(tmp_path):
    path = tmp_path / 'oauth-client.json'
    path.write_text(json.dumps({
        'installed': {'client_id': 'desktop', 'client_secret': 'desktop-secret', 'project_id': 'job-search'},
    }))
    calls = []

    def opener(request, timeout=30):
        calls.append((request.get_method(), request.full_url, request.data))
        if request.full_url.endswith('/projects/job-search'):
            return Response({'projectNumber': '42'})
        if request.full_url.endswith('/brands'):
            return Response({'brands': [{'name': 'projects/42/brands/42'}]})
        if request.full_url.endswith('/clients') and request.get_method() == 'GET':
            return Response({'clients': []})
        body = json.loads(request.data.decode())
        assert CALLBACK in body['redirectUris']
        assert LOOPBACK in body['redirectUris']
        return Response({'clientId': 'web-client', 'secret': 'web-secret'})

    client_id, secret = ensure_callback(path, opener=opener, token='token')
    assert client_id == 'web-client'
    assert secret == 'web-secret'
    saved = json.loads(path.read_text())
    assert saved['installed']['client_id'] == 'desktop'
    assert saved['web']['redirect_uris'] == [CALLBACK, LOOPBACK]
    assert calls[-1][0] == 'POST'


def test_saved_web_client_is_reused_without_another_update(tmp_path):
    path = tmp_path / 'oauth-client.json'
    path.write_text(json.dumps({'web': {
        'client_id': 'web', 'client_secret': 'secret', 'project_id': 'job-search',
        'redirect_uris': [CALLBACK, LOOPBACK],
    }}))

    def opener(*args, **kwargs):
        raise AssertionError('Google was called')

    assert ensure_callback(path, opener=opener, token='token') == ('web', 'secret')
