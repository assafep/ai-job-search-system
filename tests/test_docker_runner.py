import io
import json
import tarfile
from urllib.parse import parse_qs, urlsplit

from provisioning import docker_runner
from provisioning.contracts import Outcome


class Engine:
    def __init__(self):
        self.image = None
        self.volume = None
        self.container = None
        self.starts = []
        self.builds = 0

    def request(self, method, path, body=None, raw=None, missing=False):
        if path == '/version':
            return {'Version': 'test'}
        if path == '/images/ajss-provision/json':
            return {'Id': 'base'}
        if path.startswith('/images/'):
            return self.image
        if path.startswith('/build?'):
            self.builds += 1
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                assert 'config/runtime.json' not in archive.getnames()
                assert '.onboarding/cloud.json' not in archive.getnames()
                assert archive.getmember('grok').mode == 0o755
            self.image = {'Id': 'runner-image'}
            return {}
        if path.startswith('/volumes/'):
            if method == 'GET':
                return self.volume
            self.volume = body
            return self.volume
        if path.endswith('-init/json'):
            return None
        if path.startswith('/containers/create?') and path.endswith('-init'):
            assert body['Cmd'][0] == 'python3'
            return {'Id': 'ownership-helper'}
        if path == '/containers/ownership-helper/start':
            self.starts.append('ownership-helper')
            return {}
        if path == '/containers/ownership-helper/wait':
            return {'StatusCode': 0}
        if path == '/containers/ownership-helper' and method == 'DELETE':
            return {}
        if path.startswith('/containers/create?'):
            self.container = {'Config': body, 'Image': body['Image'], 'State': {'Running': False}}
            return {'Id': parse_qs(urlsplit(path).query)['name'][0]}
        if path.startswith('/containers/'):
            if method == 'DELETE':
                self.container = None
                return {}
            return self.container
        raise AssertionError((method, path))


def test_runner_deploy_is_private_idempotent_and_does_not_start_work(monkeypatch, tmp_path):
    (tmp_path / '.onboarding').mkdir()
    (tmp_path / '.onboarding/cloud.json').write_text(json.dumps({'runtime': {'bearer_token': 'private-token'}}))
    (tmp_path / 'requirements.txt').write_text('reportlab==5.1.0')
    from services import runtime_config
    monkeypatch.setattr(runtime_config, 'ROOT', str(tmp_path))
    (tmp_path / 'config').mkdir()
    profile = tmp_path / 'config/custom-profile.json'
    profile.write_text('{}')
    runtime = {'google_drive': {
        'connection': 'test', 'interview_journal_url': 'https://docs.google.com/document/d/journal/edit',
        'artifact_root_url': 'https://drive.google.com/drive/folders/root',
        'applicant_profile_path': 'config/custom-profile.json',
    }}
    (tmp_path / 'config/runtime.json').write_text(json.dumps(runtime))
    fallback = tmp_path / '.onboarding/codex'
    fallback.mkdir()
    (fallback / 'auth.json').write_text('{}')
    monkeypatch.delenv('AJSS_HOST_CODEX_HOME', raising=False)

    grok = tmp_path / 'grok'
    grok.write_bytes(b'\x7fELF-test')
    monkeypatch.setenv('AJSS_GROK_BINARY', str(grok))
    monkeypatch.setattr(docker_runner, 'bundle_files', lambda repo: {'services/__init__.py': b''})
    monkeypatch.setattr(docker_runner, 'load_host', lambda path: {'base_url': 'https://ajss.example'})
    engine = Engine()
    first = docker_runner.deploy(tmp_path, engine)
    second = docker_runner.deploy(tmp_path, engine)
    assert first.outcome == Outcome.CHANGED
    assert second.outcome == Outcome.UNCHANGED
    assert engine.builds == 1
    assert engine.starts == ['ownership-helper', 'ownership-helper']
    assert 'private-token' not in str(first)
    assert engine.container['State']['Running'] is False
    binds = engine.container['Config']['HostConfig']['Binds']
    assert str(profile) + ':/app/config/applicant_profile.json:ro' in binds
    assert str(fallback) + ':/var/lib/ajss/.codex' in binds
    assert json.loads((tmp_path / 'config/runtime.json').read_text()) == runtime
    translated = json.loads((tmp_path / '.onboarding/runner-runtime.json').read_text())
    assert translated['google_drive']['applicant_profile_path'] == 'config/applicant_profile.json'
    assert any('WORKLOAD_REGISTRY_PATH=/var/lib/ajss/' in value for value in engine.container['Config']['Env'])
    engine.container['State']['Running'] = True
    refused = docker_runner.deploy(tmp_path, engine)
    assert refused.outcome == Outcome.FAILED
    assert 'running' in str(refused.evidence)
    assert engine.starts == ['ownership-helper', 'ownership-helper']
