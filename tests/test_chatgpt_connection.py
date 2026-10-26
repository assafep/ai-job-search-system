import json
from pathlib import Path
from tempfile import TemporaryDirectory
from provisioning.chatgpt import prepare
from provisioning.execute import up_steps


class Response:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self):
        return b'{"Prepared": 0}'


def test_handoff_is_private_authenticated_and_repeatable():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        repo = root / 'repo'
        repo.mkdir()
        onboarding = repo / '.onboarding'
        (onboarding).mkdir(parents=True)
        (repo / 'openapi.yaml').write_text('openapi: 3.0.3\npaths: {}\n')
        checkpoint = onboarding / 'cloud.json'
        checkpoint.write_text(json.dumps({'application_ready': True, 'runtime': {
            'base_url': 'https://example.com', 'bearer_token': 'secret-test-token'}}))
        calls = []
        def opener(request, timeout):
            calls.append(request)
            assert request.get_header('Authorization') == 'Bearer secret-test-token'
            assert request.full_url == 'https://example.com/journal/queues'
            return Response()
        first = prepare(repo, onboarding, opener, mcp_verifier=lambda url, token: 30)
        assert first.outcome.value == 'changed'
        assert 'secret-test-token' not in repr(first)
        directory = repo / '.onboarding' / 'chatgpt'
        assert (directory.stat().st_mode & 0o777) == 0o700
        for path in directory.iterdir():
            assert (path.stat().st_mode & 0o777) == 0o600
        assert json.loads((directory / 'authentication.json').read_text())['token'] == 'secret-test-token'
        assert 'https://example.com' in (directory / 'openapi.yaml').read_text()
        assert prepare(repo, onboarding, opener, mcp_verifier=lambda url, token: 30).outcome.value == 'unchanged'
        assert len(calls) == 2
        checkpoint.write_text('{}')
        assert prepare(repo, onboarding, opener, mcp_verifier=lambda url, token: 30).outcome.value == 'failed'
        assert len(calls) == 2


def test_chatgpt_stage_follows_deployment():
    names = [name for name, _ in up_steps(None)]
    assert names.index('prepare_chatgpt_connection') == names.index('deploy_application') + 1
