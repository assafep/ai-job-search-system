import json
from pathlib import Path

import pytest

from application_service.repository import MemoryRepository
from services.orchestrator import Orchestrator
from services.pipeline_config import validate
from services.polling import PollingSchedule, workflow_poll_seconds
from services.workload_registry import WorkloadRegistry


def config():
    return json.loads(Path('config/pipelines.json').read_text())


def test_daily_default_and_supported_override_validation():
    value = config()
    assert value['pipelines']['interview']['event_poll_seconds'] == 86400
    assert workflow_poll_seconds('prepare-interview') == 86400
    assert workflow_poll_seconds('handle-incoming-email') == 60
    validate(value)
    value['pipelines']['interview']['event_poll_seconds'] = 3600
    validate(value)


@pytest.mark.parametrize('seconds', [True, 0, -1, 86401, 'daily', float('nan')])
def test_invalid_event_poll_intervals_are_rejected(seconds):
    value = config()
    value['pipelines']['interview']['event_poll_seconds'] = seconds
    with pytest.raises(ValueError, match='event_poll_seconds'):
        validate(value)


def test_busy_pool_does_not_consume_the_daily_poll_slot():
    now = [0]
    schedule = PollingSchedule(clock=lambda: now[0])
    assert not schedule.admit('interview', 86400, lambda: False)
    now[0] = 60
    assert schedule.admit('interview', 86400, lambda: True)
    now[0] = 86400
    assert not schedule.admit('interview', 86400, lambda: True)
    now[0] = 86460
    assert schedule.admit('interview', 86400, lambda: True)


@pytest.mark.parametrize('override', [None, 120])
def test_orchestrator_honors_interview_interval_without_delaying_email(tmp_path, monkeypatch, override):
    value = config()
    value['discovery_workers'] = 0
    value.pop('discovery_seconds', None)
    for name, spec in value['pipelines'].items():
        spec['workers'] = 1 if name in ('interview', 'correspondence') else 0
    if override is None:
        # Older configs also inherit the daily blueprint default.
        value['pipelines']['interview'].pop('event_poll_seconds')
    else:
        value['pipelines']['interview']['event_poll_seconds'] = override
    path = tmp_path/'pipelines.json'; path.write_text(json.dumps(value))
    now, calls = [0], []
    def poll(persistence, registry, workflows): calls.extend(workflows)
    runner = Orchestrator(MemoryRepository(), WorkloadRegistry(), None, str(path), event_poller=poll)
    runner.schedule.clock = lambda: now[0]
    def launch(name, callback):
        if name in ('prepare-interview', 'handle-incoming-email'):
            callback()
            return True
        return False
    monkeypatch.setattr(runner, '_launch_background', launch)
    try:
        runner._poll_workflows()
        now[0] = 60; runner._poll_workflows()
        assert calls == ['handle-incoming-email', 'prepare-interview', 'handle-incoming-email']
        boundary = override or 86400
        now[0] = boundary - 1; runner._poll_workflows()
        assert calls.count('prepare-interview') == 1
        now[0] = boundary; runner._poll_workflows()
        assert calls.count('prepare-interview') == 2
    finally:
        runner.pool.shutdown(wait=True)
