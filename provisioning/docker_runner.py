"""Deploy the configured Docker workload runner without activating workloads.

Docker Engine owns images/containers/volumes. Application data stays behind the
Application Service; deployment never starts a queue consumer.
"""
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import tarfile
import urllib.parse

from .contracts import Outcome, TaskResult
from .bootstrap.handoffs import save
from .state import ProvisioningFailure, State
REPO = Path(__file__).resolve().parents[1]

LABEL = 'ajss.installation'


class Engine:
    def __init__(self, path=None):
        self.path = path or os.environ.get('AJSS_DOCKER_SOCKET', '/run/ajss-docker.sock')

    def request(self, method, path, body=None, raw=None, missing=False):
        connection = http.client.HTTPConnection('localhost', timeout=600)
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        channel.settimeout(600)
        try:
            channel.connect(self.path)
            connection.sock = channel
            data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
            connection.request(method, path, body=data, headers={
                'Content-Type': 'application/x-tar' if raw is not None else 'application/json'})
            response = connection.getresponse()
            payload = response.read()
            if missing and response.status == 404:
                return None
            if response.status >= 400:
                raise ProvisioningFailure('Docker Engine returned HTTP %s for %s %s' %
                                          (response.status, method, path.split('?')[0]))
            if raw is not None:
                for line in payload.splitlines():
                    item = json.loads(line)
                    if item.get('error'):
                        raise ProvisioningFailure('Docker runner image build failed: ' + str(item['error'])[:300])
                return {}
            return json.loads(payload) if payload else {}
        finally:
            connection.close()
            channel.close()


def context(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for name, content in sorted(files.items()):
            item = tarfile.TarInfo(name)
            item.size = len(content)
            item.mode = 0o755 if name == 'grok' else 0o644
            archive.addfile(item, io.BytesIO(content))
    return output.getvalue()


def ensure_owned(resource, installation, kind):
    labels = resource.get('Labels') or (resource.get('Config') or {}).get('Labels') or {}
    if labels.get(LABEL) != installation:
        raise ProvisioningFailure('The Docker ' + kind + ' name is already owned by another installation.')


def runner_runtime(repo):
    """Translate only the container's cache path; preserve the operator's config."""
    from services.runtime_config import load
    runtime = load(str(repo / 'config/runtime.json'), require=True)
    profile = Path(runtime['google_drive']['applicant_profile_path']).expanduser()
    if not profile.is_absolute():
        profile = repo / profile
    profile = profile.resolve()
    if not profile.is_relative_to(repo.resolve()):
        raise ProvisioningFailure('The runner applicant cache must be inside this repository.')
    runtime['google_drive']['applicant_profile_path'] = 'config/applicant_profile.json'
    return runtime, profile


def identity(repo):
    installation = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:16]
    return installation, 'ajss-runner-' + installation


def deploy(repo=REPO, engine=None):
    engine = engine or Engine()
    try:
        version = engine.request('GET', '/version')
        host = load_host(repo / '.onboarding')
        installation, name = identity(repo)
        labels = {LABEL: installation}
        existing = engine.request('GET', '/containers/' + name + '/json', missing=True)
        if existing:
            ensure_owned(existing, installation, 'container')
            if existing.get('State', {}).get('Running'):
                raise ProvisioningFailure('The runner is running. Stop it and reconcile active work before deploying a replacement.')
        runtime, profile = runner_runtime(repo)
        runtime_path = repo / '.onboarding/runner-runtime.json'
        if not runtime_path.is_file() or json.loads(runtime_path.read_text()) != runtime:
            save(runtime_path, runtime)
        base = engine.request('GET', '/images/ajss-provision/json')
        files = bundle_files(repo)
        files['requirements.txt'] = (repo / 'requirements.txt').read_bytes()
        for filename in ('workload-effort.json', 'llm-providers.yaml', 'job-preferences.yaml', 'profile.yaml'):
            path = repo / 'config' / filename
            if path.is_file():
                files['config/' + filename] = path.read_bytes()
        grok = Path(os.environ.get('AJSS_GROK_BINARY', '/opt/ajss-host-cli/grok'))
        if not grok.is_file():
            raise ProvisioningFailure('Grok CLI is unavailable. Install grok on the host and rerun ctl.sh up.')
        files['grok'] = grok.read_bytes()
        if files['grok'][:4] != b'\x7fELF':
            raise ProvisioningFailure('The host Grok CLI is not a Linux executable compatible with this Docker host.')
        files['Dockerfile'] = b'''FROM ajss-provision
WORKDIR /app
COPY . /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir -r requirements.txt && install -m 755 /app/grok /usr/local/bin/grok && rm /app/grok
ENV PYTHONUNBUFFERED=1 HOME=/var/lib/ajss CODEX_HOME=/var/lib/ajss/.codex GROK_DISABLE_AUTOUPDATER=1
CMD ["python3", "-m", "services.worker_main", "--poll-seconds", "30", "--poll-workflows"]
'''
        digest = hashlib.sha256(base['Id'].encode() + context(files)).hexdigest()
        tag = 'ajss-runner:' + digest[:20]
        image = engine.request('GET', '/images/' + tag + '/json', missing=True)
        changed = False
        if image is None:
            print('Building Docker runner image with Codex and Grok...', flush=True)
            engine.request('POST', '/build?' + urllib.parse.urlencode({'t': tag, 'rm': '1'}), raw=context(files))
            image = engine.request('GET', '/images/' + tag + '/json')
            changed = True
        volume = engine.request('GET', '/volumes/' + name, missing=True)
        if volume is None:
            volume = engine.request('POST', '/volumes/create', {'Name': name, 'Labels': labels})
            changed = True
        ensure_owned(volume, installation, 'volume')
        # Initialize volume ownership with a short-lived helper, never a worker.
        helper_name = name + '-init'
        previous = engine.request('GET', '/containers/' + helper_name + '/json', missing=True)
        if previous:
            ensure_owned(previous, installation, 'initialization container')
            if previous.get('State', {}).get('Running'):
                engine.request('POST', '/containers/' + helper_name + '/wait')
            engine.request('DELETE', '/containers/' + helper_name)
        helper = engine.request('POST', '/containers/create?' + urllib.parse.urlencode({'name': helper_name}), {
            'Image': image['Id'], 'Labels': labels,
            'Cmd': ['python3', '-c',
                    'import os; paths=["/var/lib/ajss", "/var/lib/ajss/.codex", "/var/lib/ajss/.grok"]; '
                    '[(os.makedirs(p, mode=0o700, exist_ok=True), os.chown(p, %s, %s)) for p in paths]'
                    % (os.getuid(), os.getgid())],
            'HostConfig': {'Binds': [name + ':/var/lib/ajss']},
        })['Id']
        try:
            engine.request('POST', '/containers/' + helper + '/start')
            initialized = engine.request('POST', '/containers/' + helper + '/wait')
            if initialized.get('StatusCode') != 0:
                raise ProvisioningFailure('Runner persistent-volume initialization failed.')
        finally:
            engine.request('DELETE', '/containers/' + helper)

        shared_lock = repo / 'config/concurrent-queues.lock'
        shared_lock.touch(exist_ok=True)
        config = {
            'Image': image['Id'], 'Labels': dict(labels, **{'ajss.release': digest, 'ajss.lifecycle': '1'}),
            'User': str(os.getuid()) + ':' + str(os.getgid()),
            'Env': ['APPLICATION_SERVICE_URL=' + host['base_url'],
                    'APPLICATION_SERVICE_TOKEN=' + json.loads((repo / '.onboarding/cloud.json').read_text())['runtime']['bearer_token'],
                    'RUNTIME_CONFIG_PATH=/app/config/runtime.json',
                    'WORKLOAD_REGISTRY_PATH=/var/lib/ajss/workloads.json',
                    'AJSS_RUNNER_LOCK_PATH=/var/lib/ajss/runner.lock',
                    'AJSS_WORKER_READY_PATH=/var/lib/ajss/worker-ready',
                    'APPROVAL_REGISTRY_PATH=/var/lib/ajss/approvals.json'],
            'Healthcheck': {'Test': ['CMD', 'python3', '-m', 'services.worker_health'],
                'Interval': 1000000000, 'Timeout': 1000000000, 'Retries': 3},
            'HostConfig': {'Binds': [
                name + ':/var/lib/ajss',
                str(shared_lock) + ':/var/lib/ajss/runner.lock',
                str(runtime_path) + ':/app/config/runtime.json:ro',
                str(profile) + ':/app/config/applicant_profile.json:ro',
            ], 'RestartPolicy': {'Name': 'no'}},
        }
        for key, target in (('AJSS_HOST_CODEX_HOME', '.codex'), ('AJSS_HOST_GROK_HOME', '.grok')):
            source = os.environ.get(key)
            if not source and key == 'AJSS_HOST_CODEX_HOME':
                fallback = repo / '.onboarding/codex'
                if (fallback / 'auth.json').is_file():
                    source = str(fallback)
            if source:
                config['HostConfig']['Binds'].append(source + ':/var/lib/ajss/' + target)
        for key in ('AGENT_PROVIDER', 'CODING_AGENT', 'CODING_AGENT_MODEL', 'CODEX_MODEL'):
            if os.environ.get(key):
                config['Env'].append(key + '=' + os.environ[key])
        # Include credentials in the private comparison only, never labels/logs.
        spec = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        config['Labels']['ajss.spec'] = spec
        if existing and existing['Config']['Labels'].get('ajss.spec') != spec:
            engine.request('DELETE', '/containers/' + name)
            existing = None
        if existing is None:
            engine.request('POST', '/containers/create?' + urllib.parse.urlencode({'name': name}), config)
            changed = True
        deployed = engine.request('GET', '/containers/' + name + '/json')
        ensure_owned(deployed, installation, 'container')
        if deployed.get('Image') != image['Id'] or deployed.get('State', {}).get('Running'):
            raise ProvisioningFailure('Docker runner deployment verification failed.')
        return TaskResult(Outcome.CHANGED if changed else Outcome.UNCHANGED, None, (
            'Docker Engine ' + version['Version'], 'runner image ' + tag,
            'container ' + name, 'persistent volume ' + name,
            'Codex and Grok packaged; runner deployed without starting application workloads',
        ))
    except (OSError, ValueError, KeyError, http.client.HTTPException, ProvisioningFailure) as exc:
        return TaskResult(Outcome.FAILED, None, (str(exc),),
                          'Fix the reported Docker runner condition and rerun ./deployment/ctl.sh up.')


if __name__ == '__main__':
    with State().locked():
        result = deploy()
    print('deploy_runner: ' + result.outcome.value)
    for line in result.evidence:
        print('  ' + line)
    raise SystemExit(0 if result.outcome in (Outcome.CHANGED, Outcome.UNCHANGED) else 2)
