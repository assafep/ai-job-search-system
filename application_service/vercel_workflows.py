"""Vercel execution for the nine workflows without native ChatGPT browser steps.

Vercel Queues delivers independent work units. Persistent intents prevent a
redelivery from repeating a unit whose outcome is uncertain. No daemon or local
registry is used. The existing workflow runtime still defines business logic.
"""
from copy import deepcopy
import json
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path

from .chatgpt_workflows import guard
from .workflow_execution import CHATGPT_WORKFLOWS
from .workflow_runs import Runs, definitions, public, timestamp

TOPIC = 'ajss-workflows'
ROOT = Path(__file__).resolve().parents[1]


class ConfigurationRequired(RuntimeError):
    pass


class Busy(RuntimeError):
    pass


def provider():
    if os.environ.get('XAI_API_KEY'):
        from adapters.llm.xai_provider import XAIProvider
        return XAIProvider(timeout=120)
    if os.environ.get('OPENAI_API_KEY'):
        from adapters.llm.openai_provider import OpenAIProvider
        from functools import partial
        import urllib.request
        return OpenAIProvider(opener=partial(urllib.request.urlopen, timeout=120))
    raise ConfigurationRequired('Configure OPENAI_API_KEY or XAI_API_KEY in the Vercel production environment')


def persistence_client():
    from services.application_service_client import ApplicationServiceClient
    return ApplicationServiceClient(base_url=os.environ['AJSS_MCP_ISSUER'], auth_token=os.environ['AJSS_API_TOKEN'])


def publish_unit(run_id, unit_id):
    from vercel.queue.sync import QueueClient
    from vercel.queue import DuplicateIdempotencyKeyError
    try:
        QueueClient(timeout=15).send(TOPIC, {'run_id': run_id, 'unit_id': unit_id},
                                    idempotency_key=run_id + ':' + unit_id + ':' + datetime.now(timezone.utc).date().isoformat(), retention=86400)
    except DuplicateIdempotencyKeyError:
        pass


def notify(run_id):
    try:
        publish_unit(run_id, 'admit')
    except Exception:
        # Never include provider tokens or queue authorization errors in API output.
        raise RuntimeError('Vercel queue notification failed. The run is saved; retry startWorkflow with the same key.') from None


def readiness(repository):
    from .runtime_client import RuntimeClient
    profile = RuntimeClient(repository).get_profile() or {}
    resume = (profile.get('source_resume') or {}).get('path') or ''
    return {'chatgpt_browser_workflows': sorted(CHATGPT_WORKFLOWS),
            'chatgpt_browser_provided_by': 'ChatGPT host; not a workstation or remote browser adapter',
            'vercel_enabled': os.environ.get('AJSS_VERCEL_WORKFLOWS_ENABLED') == '1',
            'model_credential_configured': bool(os.environ.get('OPENAI_API_KEY') or os.environ.get('XAI_API_KEY')),
            'portable_resume_configured': bool(resume and not os.path.isabs(resume)),
            'artifact_creation_configured': bool(os.environ.get('AJSS_DATABASE_URL') and all(
                os.environ.get(key) for key in ('GOOGLE_DRIVE_FILE_CLIENT_ID', 'GOOGLE_DRIVE_FILE_CLIENT_SECRET', 'GOOGLE_DRIVE_FILE_REFRESH_TOKEN'))),
            'automatic_schedule': 'daily at 05:00 UTC (Hobby-compatible); prepare-interview runs daily',
            'requirements': ['Model credential for hosted agent workflows',
                             'Drive résumé and profile from completed applicant onboarding',
                             'Working Drive artifact creation permissions for bookmark and preparation',
                             'Explicit cloud activation after readiness verification']}


def execute_unit(record, unit):
    from .hosted_registry import HostedWorkloads, HostedApprovals
    from services.workflow_runtime import WorkflowRuntime
    from services.event_dispatcher import dispatch
    from services.workload_runner import _get_agents
    from services.workflow_runner import run_one
    name = record['request']['workflow']
    if name in CHATGPT_WORKFLOWS:
        raise ValueError('Native ChatGPT browser workflows cannot execute on Vercel')
    client = persistence_client()
    registry = HostedWorkloads()
    runtime = WorkflowRuntime(registry, client, HostedApprovals())
    selected_provider = provider() if _get_agents(name) else False
    if unit['kind'] == 'queue':
        app = client.get_application(unit['application_id'])
        if not app or app['status'] not in definitions()[name]['input_states']:
            return {'status': 'skipped', 'application_id': unit['application_id'], 'workflow': name}
        if name == 'prepare-validated':
            profile = client.get_profile()
            path = (profile.get('source_resume') or {}).get('path') or ''
            if not path or os.path.isabs(path):
                raise ConfigurationRequired('Complete applicant onboarding so the original résumé is available through the Application Service')
            envelope = dispatch(definitions()[name]['trigger']['event_type'], {'application_id': unit['application_id']},
                                event_id=record['run_id'] + ':' + unit['unit_id'], registry=registry)
            return runtime.run(envelope['workload_id'], agent_provider=selected_provider, recover_incomplete=False)
        return run_one(client, registry, runtime, app, workflow_name=name,
                       agent_provider=selected_provider, run_id=record['run_id'] + ':' + unit['unit_id'])
    if unit['kind'] == 'poll':
        from services.external_event_polling import poll
        report = poll(client, registry, workflows=[name])
        pending = [r for r in registry.snapshot() if r.workflow == name and r.status == 'queued']
        return {'status': 'succeeded' if report[name]['status'] == 'ok' else 'failed',
                'workflow': name, 'external_event_polling': report,
                '_new_units': {r.workload_id: {'unit_id': r.workload_id, 'kind': 'event',
                                             'workload_id': r.workload_id, 'status': 'queued'} for r in pending}}
    if unit['kind'] == 'event':
        return runtime.run(unit['workload_id'], agent_provider=selected_provider, recover_incomplete=False)
    payload = unit.get('payload') or {}
    if name == 'discover':
        payload = runtime.prepare_context(name, payload, selected_provider)
    envelope = dispatch(definitions()[name]['trigger']['event_type'], payload,
                        event_id=record['run_id'] + ':' + unit['unit_id'], registry=registry)
    return runtime.run(envelope['workload_id'], agent_results={}, agent_provider=selected_provider,
                       recover_incomplete=False)


class Engine:
    def __init__(self, runs=None, execution_guard=guard, publisher=publish_unit, executor=execute_unit,
                 report_client=None):
        self.runs = runs or Runs()
        self.guard, self.publisher, self.executor = execution_guard, publisher, executor
        self.report_client = report_client

    def _load(self, run_id):
        with self.runs.store.locked() as records:
            record = deepcopy(records[run_id])
        if record['request']['workflow'] in CHATGPT_WORKFLOWS or record.get('execution_host') != 'vercel':
            raise ValueError('This run does not belong to Vercel')
        return record

    def _save(self, record):
        record['updated_at'] = timestamp()
        with self.runs.store.locked() as records:
            records[record['run_id']] = deepcopy(record)

    def plan(self, run_id):
        with self.guard():
            record = self._load(run_id)
            if record['status'] in {'succeeded', 'failed', 'reconciliation_required'}:
                return
            if 'vercel' not in record:
                name = record['request']['workflow']
                kind = definitions()[name]['trigger']['poll_kind']
                units = {}
                if kind == 'queue':
                    for identity in record['application_ids'] or []:
                        key = hashlib.sha256(identity.encode()).hexdigest()[:24]
                        units[key] = {'unit_id': key, 'kind': 'queue', 'application_id': identity, 'status': 'queued'}
                elif kind == 'external':
                    units['poll'] = {'unit_id': 'poll', 'kind': 'poll', 'status': 'queued'}
                elif name == 'discover':
                    import json
                    from services.discovery_sources import source_boards
                    sources = json.loads((ROOT / 'config' / 'pipelines.json').read_text())['discovery_sources']
                    for source in sources:
                        boards = source_boards(source) if source in {'greenhouse', 'ashby', 'comeet', 'lever', 'smartrecruiters'} else {None: None}
                        for board in boards:
                            key = hashlib.sha256((source + ':' + str(board)).encode()).hexdigest()[:24]
                            units[key] = {'unit_id': key, 'kind': 'scheduled', 'status': 'queued',
                                          'payload': {'source_names': [source], **({'discovery_board': board} if board else {})}}
                else:
                    units['check'] = {'unit_id': 'check', 'kind': 'scheduled', 'status': 'queued'}
                record['vercel'] = {'units': units}
                self._save(record)
            if not record['vercel']['units']:
                self._finish(record)
                return
            queued = [key for key, unit in record['vercel']['units'].items() if unit['status'] == 'queued']
        # Publish outside the execution lock. An admission redelivery republishes
        # the same unit keys safely after a partial publishing failure.
        for key in queued:
            self.publisher(run_id, key)

    def _finish(self, record):
        from services.activity_report import publish
        record.setdefault('started_at', timestamp())
        results = [u.get('result') or {'workflow': record['request']['workflow'], 'status': u['status']}
                   for u in record['vercel']['units'].values()]
        record['vercel']['finishing'] = True
        self._save(record)
        record['report'] = publish(self.report_client or persistence_client(), {
            'run_id': record['run_id'], 'started_at': record['started_at'], 'finished_at': timestamp(),
            'workload_results': results, 'remaining_requirements': []})
        record.update(status='failed' if any(u['status'] == 'failed' for u in record['vercel']['units'].values()) else 'succeeded',
                      finished_at=timestamp(), summary={'executions': len(results), 'execution_host': 'vercel'})
        record['vercel'].pop('finishing', None)
        self._save(record)

    def consume(self, run_id, unit_id):
        if unit_id == 'admit':
            return self.plan(run_id)
        with self.guard():
            record = self._load(run_id)
            if record['status'] in {'succeeded', 'failed', 'reconciliation_required'}:
                return
            with self.runs.store.locked() as records:
                if any(r['run_id'] != run_id and r['status'] in {'running', 'reconciliation_required'} for r in records.values()):
                    raise Busy('Another run is active or requires reconciliation')
            state = record['vercel']
            unit = state['units'][unit_id]
            if state.get('finishing'):
                record['status'] = 'reconciliation_required'
                self._save(record)
                return
            if unit['status'] in {'succeeded', 'failed', 'skipped'}:
                # A lost acknowledgement may follow a partial fan-out publish.
                # Re-publish queued descendants, never the completed unit.
                for key, child in state['units'].items():
                    if child['status'] == 'queued':
                        self.publisher(run_id, key)
                return
            if unit['status'] == 'running':
                unit['status'] = 'reconciliation_required'
                record['status'] = 'reconciliation_required'
                self._save(record)
                return
            unit['status'] = 'running'
            record.update(status='running', started_at=record.get('started_at') or timestamp())
            self._save(record)
            try:
                result = self.executor(record, unit)
            except ConfigurationRequired as exc:
                # Configuration is checked before any unit business effects.
                unit['status'] = 'queued'
                record['status'] = 'queued'
                record['requirements'] = [str(exc)]
                self._save(record)
                raise
            except BaseException:
                unit['status'] = 'reconciliation_required'
                record['status'] = 'reconciliation_required'
                self._save(record)
                raise
            result = json.loads(json.dumps(result, default=str))
            new_units = result.pop('_new_units', {})
            state['units'].update({key: value for key, value in new_units.items() if key not in state['units']})
            unit.update(status='failed' if result.get('status') == 'failed' or result.get('error') else 'succeeded', result=result)
            record.pop('requirements', None)
            self._save(record)
            if all(u['status'] in {'succeeded', 'failed', 'skipped'} for u in state['units'].values()):
                self._finish(record)
        for key in new_units:
            self.publisher(run_id, key)


def schedule(repository):
    if os.environ.get('AJSS_VERCEL_WORKFLOWS_ENABLED') != '1':
        return {'status': 'disabled', 'runs': []}
    runs, created = Runs(), []
    day = datetime.now(timezone.utc).date().isoformat()
    for name in definitions():
        if name in CHATGPT_WORKFLOWS:
            continue
        record = runs.start({'workflow': name, 'idempotency_key': 'daily:' + name + ':' + day}, repository)
        notify(record['run_id'])
        created.append(record['run_id'])
    # Wake previously queued units after delayed configuration or expired messages.
    for record in runs.store.snapshot().values():
        if record.get('execution_host') == 'vercel' and record['status'] in {'queued', 'running'}:
            publish_unit(record['run_id'], 'admit:' + day)
    return {'status': 'scheduled', 'runs': created}
