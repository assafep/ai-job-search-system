"""Private, atomic lifecycle checkpoints and stable secret references."""
import fcntl
import hashlib
import json
import os
import re
import shlex
from contextlib import contextmanager
from pathlib import Path
from .bootstrap.handoffs import save
from .contracts import SecretRef

ROOT = Path(__file__).resolve().parents[1]


class ProvisioningFailure(RuntimeError):
    """Sanitized diagnostic safe to display to the operator."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_env(path):
    """Parse dotenv assignments without executing shell code or substitutions."""
    result = {}
    if not Path(path).exists():
        return result
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise ProvisioningFailure('Invalid dotenv assignment; use literal KEY=value entries.')
        values = shlex.split(value, comments=True)
        if len(values) > 1:
            raise ProvisioningFailure('Quote dotenv values containing spaces.')
        result[key] = values[0] if values else ''
    return result


class State:
    def __init__(self, directory=None):
        self.directory = Path(directory or ROOT / '.onboarding')
        if self.directory.is_symlink():
            raise ProvisioningFailure('The provisioning state directory must not be a symlink.')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.path = self.directory / 'lifecycle.json'
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {'version': 1, 'tasks': {}}
        if self.data.get('version') != 1:
            raise ProvisioningFailure('Unsupported lifecycle checkpoint version.')
        self.secret_path = self.directory / 'secrets.json'
        self.values = json.loads(self.secret_path.read_text()) if self.secret_path.exists() else {}

    @contextmanager
    def locked(self):
        with (self.directory / 'provisioning.lock').open('a') as handle:
            os.chmod(handle.name, 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProvisioningFailure('Another provisioning process holds this installation lock.') from None
            # Refresh after acquiring the lock; do not overwrite another run's state.
            if self.path.exists():
                self.data = json.loads(self.path.read_text())
            if self.secret_path.exists():
                self.values = json.loads(self.secret_path.read_text())
            yield

    def checkpoint(self):
        save(self.path, self.data)

    def load(self, operation_key):
        return self.data['tasks'].get(operation_key)

    def save(self, operation_key, checkpoint):
        self.data['tasks'][operation_key] = checkpoint
        self.checkpoint()

    def put_secret(self, name, value):
        if not isinstance(value, str) or not value:
            raise ProvisioningFailure('Cannot save an empty credential: ' + name)
        version = hashlib.sha256(value.encode()).hexdigest()
        if self.values.get(name) != value:
            self.values[name] = value
            save(self.secret_path, self.values)
        return SecretRef(name, version)

    def resolve(self, reference):
        value = self.values[reference.location]
        if hashlib.sha256(value.encode()).hexdigest() != reference.version:
            raise ProvisioningFailure('Credential version changed; regenerate the deployment plan.')
        return value
