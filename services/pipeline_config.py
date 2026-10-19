"""Validated routing policy and live worker allocation, independent of agents."""

import json
import os
import tempfile
import fcntl

from persistence_api.journal_format import ALL_STATES
from persistence_api.state_machine import validate_transition
from .runtime_config import ROOT

DEFAULT_PATH = os.path.join(ROOT, 'config', 'pipelines.json')
OUTCOMES = {
    'read-posting': {'read', 'read_failure', 'system_error'},
    'score': {'validated', 'invalidated', 'duplicate', 'system_error'},
    'application': {'prepared', 'failed', 'duplicate', 'system_error'},
    'auto-apply': {'submitted', 'unconfirmed', 'waiting', 'failed', 'system_error'},
    'correspondence': {'processed', 'interview', 'rejected', 'offered', 'failed', 'system_error'},
    'interview-prep': {'prepared', 'failed', 'system_error'},
}


def validate(config):
    for field in ('poll_seconds', 'scan_seconds', 'event_poll_seconds'):
        value = config.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 86400:
            raise ValueError(field + ' must be between 0 and 86400 seconds')
    pipelines = config.get('pipelines')
    if not isinstance(pipelines, dict) or not pipelines:
        raise ValueError('pipelines must be a nonempty object')
    queues = set()
    total = 0
    discovery_workers = config.get('discovery_workers', 0)
    if isinstance(discovery_workers, bool) or not isinstance(discovery_workers, int) or not 0 <= discovery_workers <= 64:
        raise ValueError('discovery_workers must be an integer between 0 and 64')
    total += discovery_workers
    for name, spec in pipelines.items():
        if not isinstance(spec, dict) or spec.get('agent') not in OUTCOMES:
            raise ValueError('unknown agent for pipeline ' + name)
        count = spec.get('workers')
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 64:
            raise ValueError('workers must be an integer between 0 and 64: ' + name)
        total += count
        states = spec.get('states')
        if not isinstance(states, list) or not states or any(state not in ALL_STATES for state in states):
            raise ValueError('invalid input states: ' + name)
        event = spec.get('event_type')
        if event not in (None, 'incoming_email', 'calendar_event'):
            raise ValueError('unsupported event_type: ' + name)
        if spec['agent'] == 'correspondence' and event != 'incoming_email':
            raise ValueError('correspondence requires incoming_email events')
        if spec['agent'] == 'interview-prep' and event != 'calendar_event':
            raise ValueError('interview preparation requires calendar_event events')
        if spec['agent'] == 'auto-apply' and states != ['Prepared']:
            raise ValueError('automatic submission may consume only Prepared')
        if spec['agent'] == 'application' and states != ['Validated']:
            raise ValueError('preparation consumes Validated positions')
        routes = spec.get('routes')
        if not isinstance(routes, dict) or set(routes) != OUTCOMES[spec['agent']]:
            raise ValueError('routes must cover every agent outcome: ' + name)
        if routes['system_error'] != 'Dead Letters Queue':
            raise ValueError('system errors must route to Dead Letters Queue')
        for outcome, target in routes.items():
            if target is not None:
                for state in states:
                    validate_transition(state, target)
            if target == 'Applied' and (spec['agent'] != 'auto-apply' or outcome != 'submitted'):
                raise ValueError('only a confirmed submission can route to Applied')
        if spec['agent'] == 'application' and (routes['prepared'] != 'Prepared' or routes['failed'] != 'Manual Apply Required' or routes['duplicate'] != 'Duplicate'):
            raise ValueError('preparation routes must follow the application policy')
        if spec['agent'] == 'score' and routes.get('duplicate') != 'Duplicate':
            raise ValueError('score duplicates route to Duplicate')
        if spec['agent'] == 'auto-apply' and any(routes[key] != 'Manual Apply Required' for key in ('failed', 'unconfirmed')):
            raise ValueError('failed/unconfirmed submissions require manual reconciliation')
        for state in states:
            key = (state, event)
            if key in queues and event is not None:
                raise ValueError('multiple pipelines own the same state/event queue')
            if event is not None:
                queues.add(key)
    if 'discovery_seconds' in config:
        value = config['discovery_seconds']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 86400:
            raise ValueError('discovery_seconds must be between 0 and 86400 seconds')
    if 'discovery_sources' in config:
        sources = config['discovery_sources']
        from .discovery_sources import SOURCE_NAMES
        allowed = SOURCE_NAMES
        if (not isinstance(sources, list) or not sources
                or any(not isinstance(name, str) or name not in allowed for name in sources)):
            raise ValueError('discovery_sources must list supported sources: ' + ', '.join(sorted(allowed)))
    if total > 64:
        raise ValueError('at most 64 consumers may be allocated')
    return config


def load(path=DEFAULT_PATH):
    with open(path) as handle:
        return validate(json.load(handle))


def atomic_json(path, value):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.pipeline-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(value, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def scale(path, name, count):
    with open(path + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = load(path)
        if name not in config['pipelines']:
            raise ValueError('unknown pipeline: ' + name)
        config['pipelines'][name]['workers'] = count
        atomic_json(path, validate(config))
        return config
