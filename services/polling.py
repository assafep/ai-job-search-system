"""Shared clock and blueprint-driven admission for all stage runners.

Events are internal work envelopes, not scheduling triggers. Polling callbacks
read queues or adapters; scheduled stages use durable UTC minute-bucket IDs.
"""

from datetime import datetime, timezone
import hashlib
import json
from time import monotonic

from .event_dispatcher import dispatch
from .stage_definitions import discover_stages, parse_stage


POLL_SECONDS = 60
JOURNAL_TAB_LIMIT = 100


def discovery_capacity(persistence):
    return max(0, JOURNAL_TAB_LIMIT - len(persistence.journal_tabs()))


def definitions():
    return {name: parse_stage(path) for name, path in discover_stages().items()}


def stage_poll_seconds(name):
    """Default automatic polling interval declared by the stage blueprint."""
    cadence = definitions()[name]['trigger'].get('cadence', 'minute')
    intervals = {'minute': POLL_SECONDS, 'day': 86400}
    if cadence not in intervals:
        raise ValueError('Unsupported stage cadence: ' + str(cadence))
    return intervals[cadence]


class PollingSchedule:
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.last = {}

    def admit(self, name, seconds, callback):
        """Advance an automatic poll's timer only after a worker accepts it."""
        now = self.clock()
        if now - self.last.get(name, float('-inf')) < seconds:
            return False
        accepted = callback()
        if accepted:
            self.last[name] = now
        return bool(accepted)

    def run(self, name, seconds, callback):
        """Run a due poll; a failed poll remains due on the next tick."""
        now = self.clock()
        if now - self.last.get(name, float('-inf')) < seconds:
            return None
        self.last[name] = now
        return callback()


def enqueue_scheduled(registry, *, names=None, payloads=None, now=None):
    """Admit scheduled polls once per UTC minute across restarts and entrypoints."""
    now = now or datetime.now(timezone.utc)
    created = []
    for name, definition in definitions().items():
        if definition['trigger']['poll_kind'] != 'scheduled' or (names is not None and name not in names):
            continue
        active = next((record for record in registry.snapshot()
                       if record.stage == name and record.status in {'queued', 'running', 'failed'}), None)
        if active is not None:
            created.append({'workload_id': active.workload_id, 'stage': name,
                            'event': active.event, 'status': active.status})
            continue
        envelope = dispatch(definition['trigger']['event_type'], (payloads or {}).get(name, {}),
                            'poll-{}-{}'.format(name, int(now.timestamp()) // POLL_SECONDS), registry)
        created.append(envelope)
    return created


def maintenance_names():
    return [name for name, definition in definitions().items()
            if definition['trigger']['poll_kind'] == 'scheduled' and name != 'discover']


def enqueue_queues(persistence, registry, names=None):
    """Poll each state queue through the same blueprint-defined admission path."""
    created = []
    for name, definition in definitions().items():
        if definition['trigger']['poll_kind'] != 'queue' or (names is not None and name not in names):
            continue
        for state in definition['input_states']:
            for application in persistence.list_applications(status=state) or []:
                identity = application['application_id']
                # An item is never assigned twice while its prior work is active.
                if any(record.stage == name and record.status in {'queued', 'running', 'failed', 'awaiting_input'}
                       and record.event['payload'].get('application_id') == identity
                       for record in registry.snapshot()):
                    continue
                version = {key: application.get(key) for key in
                           ('application_id', 'status', 'updated_at', 'last_event_id')}
                admission = hashlib.sha256(json.dumps(version, sort_keys=True).encode()).hexdigest()[:24]
                created.append(dispatch(definition['trigger']['event_type'], {'application_id': identity},
                                        'poll-{}-{}'.format(name, admission), registry))
    return created
