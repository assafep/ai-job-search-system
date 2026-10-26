"""Service-owned durable workflow requests; execution belongs to a separate worker.

The existing runtime table needs no new DDL. One installation-wide lock makes
idempotent admission and claims atomic across HTTP/serverless instances.
Running work is never reclaimed automatically: a crashed submission is uncertain.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import uuid
from .workflow_execution import execution_host, execution_enabled
from urllib.parse import parse_qs, urlparse


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def definitions():
    from services.polling import definitions as load
    return load()


def blueprint_digest(name):
    return hashlib.sha256(json.dumps(definitions()[name], sort_keys=True).encode()).hexdigest()


def list_workflows():
    return {'workflows': [dict(name=name, kind=d['trigger']['poll_kind'], cadence=d['trigger']['cadence'],
                              input_states=d.get('input_states', []),
                              execution_host=execution_host(name), execution_enabled=execution_enabled(name),
                              scope='all, application_id, or limit' if d['trigger']['poll_kind'] == 'queue' else 'all')
                          for name, d in definitions().items()]}


def validate_request(data):
    if not isinstance(data, dict) or set(data) - {'workflow', 'application_id', 'limit', 'idempotency_key'}:
        raise ValueError('Unknown workflow request fields')
    for key in ('workflow', 'idempotency_key'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(key + ' must be a non-empty string')
    if len(data['idempotency_key']) > 200:
        raise ValueError('idempotency_key is too long')
    definition = definitions().get(data['workflow'])
    if definition is None:
        raise ValueError('Unknown workflow')
    if 'limit' in data and (type(data['limit']) is not int or data['limit'] < 1):
        raise ValueError('limit must be a positive integer')
    if 'application_id' in data and (not isinstance(data['application_id'], str) or not data['application_id'].strip()):
        raise ValueError('application_id must be non-empty')
    if 'application_id' in data and 'limit' in data:
        raise ValueError('Choose application_id or limit')
    if definition['trigger']['poll_kind'] != 'queue' and set(data) & {'application_id', 'limit'}:
        raise ValueError('Scheduled and external polls require whole-workflow scope')
    return {key: value for key, value in data.items() if key != 'idempotency_key'}


class Store:
    def snapshot(self):
        """Read without rewriting the registry or acquiring a worker claim."""
        from .database import connect
        from .initialization import resource
        with connect() as connection:
            return resource(connection, 'workflow_runs') or {}

    @contextmanager
    def locked(self):
        from .database import connect, installation_lock
        from .initialization import resource, save_resource
        with connect() as connection, installation_lock(connection, 'workflow-runs'):
            records = resource(connection, 'workflow_runs') or {}
            yield records
            save_resource(connection, 'workflow_runs', records)


class Runs:
    def __init__(self, store=None):
        self.store = store or Store()

    def start(self, data, repository):
        if os.environ.get('AJSS_WORKFLOW_RUNS_ENABLED') != '1':
            raise RuntimeError('Workflow execution is disabled; configure and start the workflow worker first')
        request = validate_request(data)
        identity = 'chat-' + hashlib.sha256(data['idempotency_key'].encode()).hexdigest()[:32]
        with self.store.locked() as records:
            if identity in records:
                if records[identity]['request'] != request:
                    raise ValueError('idempotency_key already used with another request')
                return public(records[identity])
            definition = definitions()[request['workflow']]
            selected = None
            if definition['trigger']['poll_kind'] == 'queue':
                states = definition['input_states']
                applications = [a for a in repository.list_applications()
                                if (a.status if hasattr(a, 'status') else a['status']) in states]
                ids = list(dict.fromkeys(a.application_id if hasattr(a, 'application_id') else a['application_id'] for a in applications))
                if 'application_id' in request:
                    if request['application_id'] not in ids:
                        raise ValueError('Application is not eligible for this workflow')
                    ids = [request['application_id']]
                selected = ids[:request['limit']] if 'limit' in request else ids
            record = dict(run_id=identity, request=request, application_ids=selected,
                          execution_host=execution_host(request['workflow']),
                          blueprint_digest=blueprint_digest(request['workflow']),
                          status='queued', created_at=timestamp(), updated_at=timestamp())
            records[identity] = record
            return public(record)

    def get(self, run_id):
        with self.store.locked() as records:
            if run_id not in records:
                raise KeyError(run_id)
            return public(records[run_id])

    def list(self, status=None, workflow=None, limit=50, cursor=None):
        if status is not None and status not in {
            'queued', 'running', 'succeeded', 'failed', 'reconciliation_required'
        }:
            raise ValueError('Invalid run status')
        if workflow is not None and (not isinstance(workflow, str) or not workflow.strip()):
            raise ValueError('workflow must be non-empty')
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('limit must be between 1 and 200')
        records = self.store.snapshot()
        order = lambda record: (record['created_at'], record['run_id'])
        if cursor is not None and cursor not in records:
            raise ValueError('Invalid run cursor')
        rows = sorted((record for record in records.values()
                       if (status is None or record['status'] == status)
                       and (workflow is None or record['request']['workflow'] == workflow)
                       and (cursor is None or order(record) < order(records[cursor]))),
                      key=order, reverse=True)
        page = rows[:limit]
        return {'runs': [public(record) for record in page],
                'next_cursor': page[-1]['run_id'] if len(rows) > limit else None}

    def claim(self, worker_id):
        if os.environ.get('AJSS_LEGACY_WORKSTATION_WORKFLOWS') != '1':
            return {'run': None}
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError('worker_id is required')
        with self.store.locked() as records:
            if any(r['status'] in {'running', 'reconciliation_required'} for r in records.values()):
                return {'run': None}
            from .workflow_execution import CHATGPT_WORKFLOWS
            record = next((r for r in records.values() if r['status'] == 'queued'
                           and r['request']['workflow'] not in CHATGPT_WORKFLOWS
                           and r.get('execution_host', 'workstation') == 'workstation'), None)
            if record is None:
                return {'run': None}
            record.update(status='running', worker_id=worker_id, claim_token=uuid.uuid4().hex,
                          started_at=timestamp(), updated_at=timestamp())
            return {'run': dict(record)}

    def reconcile(self, run_id, data):
        if data.get('worker_stopped') is not True or not isinstance(data.get('note'), str) or not data['note'].strip():
            raise ValueError('Stop the worker and provide a reconciliation note')
        with self.store.locked() as records:
            record = records.get(run_id)
            if record is None:
                raise KeyError(run_id)
            if record['status'] not in {'running', 'reconciliation_required'}:
                raise ValueError('Only interrupted or uncertain runs need reconciliation')
            record.update(status='failed', reconciled_at=timestamp(), updated_at=timestamp(),
                          reconciliation_note=data['note'])
            record.pop('claim_token', None)
            return public(record)

    def cancel(self, run_id, reason=None):
        """Cancel a queued or running workflow run. Only queued runs can be cleanly cancelled."""
        with self.store.locked() as records:
            record = records.get(run_id)
            if record is None:
                raise KeyError(run_id)
            if record['status'] not in {'queued', 'running'}:
                raise ValueError('Only queued or running runs can be cancelled; current status is ' + record['status'])
            record.update(status='failed', cancelled_at=timestamp(), updated_at=timestamp(),
                          summary={'cancelled': True, 'reason': reason or 'User cancelled'})
            record.pop('claim_token', None)
            record.pop('worker_id', None)
            return public(record)

    def update(self, run_id, data):
        with self.store.locked() as records:
            record = records.get(run_id)
            if record is None:
                raise KeyError(run_id)
            if data.get('claim_token') != record.get('claim_token') or not record.get('claim_token'):
                raise ValueError('Invalid worker claim')
            status = data.get('status')
            if status not in {'running', 'succeeded', 'failed', 'reconciliation_required'}:
                raise ValueError('Invalid run status')
            if record['status'] != 'running':
                if record['status'] == status:
                    return public(record)
                raise ValueError('Finished runs cannot be changed or retried')
            record.update(status=status, updated_at=timestamp())
            for key in ('report', 'code_revision', 'summary'):
                if key in data:
                    record[key] = data[key]
            if status != 'running':
                record['finished_at'] = timestamp()
            return public(record)


def public(record):
    result = {key: value for key, value in record.items() if key not in {'claim_token', 'worker_id', 'chatgpt'}}
    if record.get('chatgpt'):
        tasks = record['chatgpt']['tasks'].values()
        result['progress'] = {'admitted': len(record.get('application_ids') or []),
                              'completed': sum(t['state'] == 'completed' for t in tasks),
                              'pending': [{'task_id': t['task_id'], 'state': t['state'],
                                           'pause_reason': t.get('pause_reason')}
                                          for t in tasks if t['state'] != 'completed']}
    return result


def handle(handler, method, parts, repository, payload=None):
    """Called only after normal Application Service bearer authentication."""
    runs = Runs()
    if len(parts) == 4 and parts[0] == 'workflow-runs' and parts[2] == 'chatgpt' and method == 'POST':
        from .chatgpt_workflows import Sessions
        from .mcp_adapter import Catalog
        actions = {
            'next': ('nextChatGPTWorkflowTask', lambda sessions: sessions.next(parts[1])),
            'validation': ('completeChatGPTValidation', lambda sessions: sessions.validate(parts[1], payload)),
            'begin-submission': ('beginChatGPTSubmission', lambda sessions: sessions.begin_submission(parts[1], payload)),
            'submission': ('completeChatGPTSubmission', lambda sessions: sessions.complete_submission(parts[1], payload)),
            'pause': ('pauseChatGPTWorkflowTask', lambda sessions: sessions.pause(parts[1], payload)),
            'package': ('getChatGPTSubmissionPackage', lambda sessions: sessions.package(parts[1], payload['task_id'], payload['token'])),
        }
        if parts[3] not in actions:
            return handler.send_json(404, {'error': 'not found'})
        operation, action = actions[parts[3]]
        Catalog(profile='domain').request(operation, {'run_id': parts[1], 'body': payload or {}})
        return handler.send_json(200, action(Sessions(repository, runs)))
    if method == 'GET' and parts == ['workflows', 'readiness']:
        return handler.send_json(200, {'status': 'not-configured'})
    if method == 'GET' and parts == ['workflows']:
        return handler.send_json(200, list_workflows())
    if method == 'GET' and parts == ['workflow-runs']:
        query = parse_qs(urlparse(handler.path).query, keep_blank_values=True)
        if set(query) - {'status', 'workflow', 'limit', 'cursor'} or any(len(v) != 1 for v in query.values()):
            raise ValueError('Unknown or repeated run-list parameters')
        arguments = {key: values[0] for key, values in query.items()}
        if 'limit' in arguments:
            try:
                arguments['limit'] = int(arguments['limit'])
            except ValueError:
                raise ValueError('limit must be an integer') from None
        return handler.send_json(200, runs.list(**arguments))
    if method == 'POST' and parts == ['workflow-runs']:
        record = runs.start(payload, repository)
        return handler.send_json(202, record)
    if method == 'POST' and parts == ['workflow-runs', 'claim']:
        return handler.send_json(200, runs.claim(payload.get('worker_id')))
    if len(parts) == 2 and parts[0] == 'workflow-runs' and method == 'GET':
        return handler.send_json(200, runs.get(parts[1]))
    if len(parts) == 3 and parts[0] == 'workflow-runs' and parts[2] == 'reconcile' and method == 'POST':
        return handler.send_json(200, runs.reconcile(parts[1], payload))
    if len(parts) == 3 and parts[0] == 'workflow-runs' and parts[2] == 'cancel' and method == 'POST':
        reason = (payload or {}).get('reason')
        return handler.send_json(200, runs.cancel(parts[1], reason))
    if len(parts) == 3 and parts[0] == 'workflow-runs' and parts[2] == 'worker-update' and method == 'POST':
        return handler.send_json(200, runs.update(parts[1], payload))
    if len(parts) == 3 and parts[0] == 'workflow-runs' and parts[2] == 'report' and method == 'GET':
        record = runs.get(parts[1])
        if not record.get('report'):
            return handler.send_json(409, {'error': 'PDF report is not available yet', 'status': record['status']})
        result = repository.read_file(record['report']['path'])
        import base64
        return handler.send_json(200, {'mime_type': 'application/pdf', 'file': result['file'],
                                     'content_base64': base64.b64encode(result['content']).decode('ascii')})
    return handler.send_json(404, {'error': 'not found'})
