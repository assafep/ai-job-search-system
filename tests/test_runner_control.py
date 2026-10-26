from copy import deepcopy

import pytest

from provisioning import runner_control
from provisioning.contracts import Outcome
from provisioning.docker_runner import LABEL, identity


class Engine:
    def __init__(self, repo, running=False):
        installation, self.name = identity(repo)
        self.container = {'Config': {'Labels': {LABEL: installation, 'ajss.lifecycle': '1'}},
                          'State': {'Running': running, 'Health': {'Status': 'healthy'}}}
        self.calls = []
        self.drain = True
        self.exit_on_start = False

    def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path))
        if method == 'GET':
            return deepcopy(self.container)
        if path.endswith('/start'):
            self.container['State']['Running'] = not self.exit_on_start
        elif path.endswith('/kill?signal=SIGTERM'):
            if self.drain:
                self.container['State']['Running'] = False
        else:
            pytest.fail('unexpected mutation: ' + path)
        return {}


def test_start_stop_and_status_are_idempotent_and_never_provision(tmp_path):
    engine = Engine(tmp_path)
    checks = []
    assert runner_control.control('start', tmp_path, engine, check=checks.append).outcome == Outcome.CHANGED
    assert runner_control.control('start', tmp_path, engine, check=checks.append).outcome == Outcome.UNCHANGED
    assert len(checks) == 2
    assert runner_control.control('status', tmp_path, engine).outcome == Outcome.UNCHANGED
    assert runner_control.control('stop', tmp_path, engine).outcome == Outcome.CHANGED
    assert runner_control.control('stop', tmp_path, engine).outcome == Outcome.UNCHANGED
    mutations = [(method, path) for method, path in engine.calls if method != 'GET']
    assert mutations == [('POST', '/containers/' + engine.name + '/start'),
                         ('POST', '/containers/' + engine.name + '/kill?signal=SIGTERM')]


@pytest.mark.parametrize('condition', ['missing', 'foreign', 'old', 'paused', 'bad_api'])
def test_failed_preflight_never_starts_runner(tmp_path, condition):
    engine = Engine(tmp_path)
    if condition == 'missing':
        engine.container = None
    elif condition == 'foreign':
        engine.container['Config']['Labels'][LABEL] = 'other'
    elif condition == 'old':
        engine.container['Config']['Labels'].pop('ajss.lifecycle')
    elif condition == 'paused':
        engine.container['State']['Paused'] = True
    def check(container):
        if condition == 'bad_api':
            raise runner_control.ProvisioningFailure('API not ready')
    result = runner_control.control('start', tmp_path, engine, check=check)
    assert result.outcome == Outcome.FAILED
    assert all(method == 'GET' for method, _ in engine.calls)


def test_stop_timeout_does_not_force_kill(tmp_path, monkeypatch):
    engine = Engine(tmp_path, running=True)
    engine.drain = False
    times = iter([0, 2])
    monkeypatch.setattr(runner_control.time, 'monotonic', lambda: next(times))
    result = runner_control.control('stop', tmp_path, engine, timeout=1)
    assert result.outcome == Outcome.BLOCKED
    assert 'not force-killed' in str(result.evidence)
    assert engine.container['State']['Running']


def test_crashed_start_is_reported_without_retry(tmp_path):
    engine = Engine(tmp_path)
    engine.exit_on_start = True
    result = runner_control.control('start', tmp_path, engine, check=lambda _: None)
    assert result.outcome == Outcome.FAILED
    assert 'exited during startup' in str(result.evidence)
    assert sum(path.endswith('/start') for _, path in engine.calls) == 1


def test_application_preflight_checks_readiness_authentication_and_execution_owner(monkeypatch):
    import io
    import json
    requests = []
    cloud_enabled = [False]
    class Response(io.BytesIO):
        status = 200
    def open_request(request, **kwargs):
        requests.append(request)
        return Response(json.dumps({'ok': True, 'workstation_enabled': cloud_enabled[0]}).encode())
    monkeypatch.setattr(runner_control.urllib.request, 'urlopen', open_request)
    container = {'Config': {'Env': ['APPLICATION_SERVICE_URL=https://ajss.example',
                                     'APPLICATION_SERVICE_TOKEN=private-token']}}
    runner_control.check_application(container)
    assert [request.full_url for request in requests] == [
        'https://ajss.example/ready', 'https://ajss.example/journal/queues',
        'https://ajss.example/workflows/readiness']
    assert all(request.get_header('Authorization') == 'Bearer private-token' for request in requests)
    cloud_enabled[0] = True
    with pytest.raises(runner_control.ProvisioningFailure, match='Hosted workflows'):
        runner_control.check_application(container)


def test_lifecycle_cli_routes_under_installation_lock(tmp_path, monkeypatch, capsys):
    from provisioning import __main__, state
    from provisioning.contracts import TaskResult
    state_class = state.State
    calls = []
    def control(action, **kwargs):
        # A second lifecycle operation cannot acquire the lock while controlling Docker.
        with pytest.raises(state.ProvisioningFailure, match='installation lock'):
            with state_class(tmp_path).locked():
                pass
        calls.append((action, kwargs['timeout']))
        return TaskResult(Outcome.UNCHANGED, None, ('observed',))
    monkeypatch.setattr(state, 'State', lambda: state_class(tmp_path))
    monkeypatch.setattr(runner_control, 'control', control)
    for action in ('start', 'stop', 'status'):
        assert __main__.main([action, '--timeout', '12']) == 0
    assert calls == [('start', 12), ('stop', 12), ('status', 12)]
    assert 'observed' in capsys.readouterr().out
