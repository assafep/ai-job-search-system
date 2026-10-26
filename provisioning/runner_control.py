"""Activate or drain an existing Docker runner; never provision resources."""
import http.client
import json
import time
import urllib.error
import urllib.request

from .contracts import Outcome, TaskResult
from pathlib import Path
from .docker_runner import Engine, ensure_owned, identity
from .state import ProvisioningFailure
REPO = Path(__file__).resolve().parents[1]


def check_application(container):
    values = dict(item.split('=', 1) for item in container['Config'].get('Env', []) if '=' in item)
    url = values.get('APPLICATION_SERVICE_URL', '').rstrip('/')
    token = values.get('APPLICATION_SERVICE_TOKEN')
    if not url.startswith(('https://', 'http://')) or not token:
        raise ProvisioningFailure('Runner application URL or credential is missing; rerun ctl.sh up.')
    for path in ('/ready', '/journal/queues', '/stages/readiness'):
        request = urllib.request.Request(url + path, headers={'Authorization': 'Bearer ' + token})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.loads(response.read())
                if response.status != 200 or (path == '/ready' and (not isinstance(body, dict) or body.get('ok') is not True)):
                    raise ProvisioningFailure('Application readiness check failed for ' + path)
                if path == '/stages/readiness':
                    pass  # Readiness check is handled by the stage execution config
        except (OSError, ValueError) as exc:
            raise ProvisioningFailure('Application readiness check failed for ' + path) from None


def control(action, repo=REPO, engine=None, timeout=60, check=check_application):
    engine = engine or Engine()
    installation, name = identity(repo)
    path = '/containers/' + name
    try:
        if action not in ('start', 'stop', 'status'):
            raise ProvisioningFailure('Unknown runner action.')
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            raise ProvisioningFailure('Timeout must be between 1 and 3600 seconds.')
        container = engine.request('GET', path + '/json', missing=True)
        if container is None:
            raise ProvisioningFailure('Runner is not deployed. Run ./deployment/ctl.sh up first.')
        ensure_owned(container, installation, 'container')
        state = container.get('State') or {}
        if action == 'status':
            status = state.get('Status') or ('running' if state.get('Running') else 'stopped')
            health = (state.get('Health') or {}).get('Status', 'unavailable')
            return TaskResult(Outcome.UNCHANGED, None, ('container ' + name, 'state: ' + status, 'worker readiness: ' + health))
        if state.get('Paused') or state.get('Restarting'):
            raise ProvisioningFailure('Runner is paused or restarting; resolve its Docker state first.')
        if action == 'stop' and not state.get('Running'):
            return TaskResult(Outcome.UNCHANGED, None, ('Runner is already stopped: ' + name,))
        if container['Config'].get('Labels', {}).get('ajss.lifecycle') != '1':
            raise ProvisioningFailure('Runner needs graceful lifecycle support. Stop the old runner and rerun ./deployment/ctl.sh up.')
        changed = False
        if action == 'start':
            check(container)
            if not state.get('Running'):
                engine.request('POST', path + '/start')
                changed = True
        else:
            # Docker stop sends SIGKILL after its deadline. Signal directly so a
            # long application attempt can drain safely beyond our CLI timeout.
            engine.request('POST', path + '/kill?signal=SIGTERM')
            changed = True
        deadline = time.monotonic() + timeout
        while True:
            current = engine.request('GET', path + '/json')
            ensure_owned(current, installation, 'container')
            state = current.get('State') or {}
            if action == 'stop' and not state.get('Running'):
                if state.get('OOMKilled') or state.get('ExitCode', 0) != 0:
                    raise ProvisioningFailure('Runner stopped with an error; inspect logs and reconcile interrupted work before restarting.')
                return TaskResult(Outcome.CHANGED, None, ('Runner stopped after draining: ' + name, 'Application Service remains available.'))
            if action == 'start':
                if not state.get('Running'):
                    raise ProvisioningFailure('Runner exited during startup (exit code %s). Inspect Docker logs; no automatic retry was made.' % state.get('ExitCode'))
                if (state.get('Health') or {}).get('Status') == 'healthy':
                    return TaskResult(Outcome.CHANGED if changed else Outcome.UNCHANGED, None,
                                      ('Runner is running and ready: ' + name, 'Native browser stages still require the ChatGPT host.'))
            if time.monotonic() >= deadline:
                return TaskResult(Outcome.BLOCKED, None,
                                  (('Runner is still draining; it was not force-killed.' if action == 'stop' else 'Runner started but readiness is not confirmed.'),),
                                  'Run ./deployment/ctl.sh status and inspect Docker logs; rerun ' + action + ' to check again.')
            time.sleep(.2)
    except (OSError, ValueError, KeyError, http.client.HTTPException, ProvisioningFailure) as exc:
        return TaskResult(Outcome.FAILED, None, (str(exc),), 'Resolve the reported condition before retrying; no resources were provisioned.')
