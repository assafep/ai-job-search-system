"""Durable ChatGPT run consumer. Never launch this inside an HTTP request.

Run on the configured stage host, exclusively with other registry writers.
A lost/crashed claim blocks admission until an operator reconciles it; neither
this worker nor the HTTP service retries running submissions automatically.
"""
import argparse
import fcntl
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .application_service_client import ApplicationServiceClient
from .runtime_config import load, ROOT


def revision():
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() + ('+dirty' if dirty.stdout else '') if result.returncode == 0 else 'unknown'


def execute_local_submission(record, persistence):
    """Run the prepared Comeet scope with durable one-shot receipts.

    Never use generic queue execution for this stage: that path has no form
    interaction. The Application Service remains the only persistence writer.
    """
    from application_service.stage_execution import submission_backend
    from application_service.stage_tasks import blueprint_digest, timestamp
    from adapters.job_boards.framework import get_framework
    from services.ready_submission import ReadySubmission
    from services.workload_registry import registry_from_environment
    from services.activity_report import publish

    if record.get('execution_host') != 'workstation' or submission_backend() != 'local-playwright':
        raise RuntimeError('Local submission backend must match the admitted task and worker configuration')
    if record['blueprint_digest'] != blueprint_digest('submit-prepared'):
        raise RuntimeError('Submission blueprint changed; reconcile the admitted task')
    if not persistence.journal_status()['valid']:
        raise RuntimeError('Journal validation failed')
    ids = record.get('application_ids') or []
    if not ids:
        raise RuntimeError('Submission task has no admitted applications')
    submitter = ReadySubmission(persistence, registry_from_environment())
    submitter.browser_backend = 'local-playwright'
    report = dict(stage_task_id=record['stage_task_id'], started_at=record['started_at'],
                  workload_results=[], code_revision=revision())
    for application_id in ids:
        application = persistence.get_application(application_id)
        # A previously closed or changed posting must never be resubmitted.
        if not application or application.get('status') != 'Prepared':
            report['workload_results'].append(dict(
                application_id=application_id, status='failed',
                error='Application is no longer Prepared; no submission attempted'))
            continue
        if get_framework().resolve_provider(application.get('posting_url', '')) != 'comeet':
            report['workload_results'].append(dict(
                application_id=application_id, status='failed',
                error='Local Playwright currently supports Comeet only; no submission attempted'))
            continue
        # Receipt is written before dispatch, and replay never re-clicks submit.
        outcome = submitter.execute(application_id, record['stage_task_id'] + ':' + application_id)
        state = outcome.get('status')
        report['workload_results'].append(dict(
            application_id=application_id,
            status='ok' if state in {'Submitted successfully', 'Already submitted'} else 'failed',
            submission_status=state,
            error='; '.join(outcome.get('blockers') or []) if state not in {'Submitted successfully', 'Already submitted'} else None))
    report['finished_at'] = timestamp()
    report['activity_report_pdf'] = publish(persistence, report)
    return report


def execute(record, persistence):
    from application_service.stage_execution import CHATGPT_STAGES
    if record['request']['stage'] == 'submit-prepared' and record.get('execution_host') == 'workstation':
        return execute_local_submission(record, persistence)
    if record['request']['stage'] in CHATGPT_STAGES:
        raise ValueError('This stage requires ChatGPT native browser execution through MCP task tools')
    from .concurrent_queues import run as run_queues
    from .polling import definitions, enqueue_scheduled
    from .workload_registry import registry_from_environment
    from .stage_executor import runtime_from_environment
    from .stage_runner import _get_provider
    from .activity_report import publish
    from application_service.stage_tasks import timestamp
    registry = registry_from_environment()
    runtime = runtime_from_environment(persistence_client=persistence)
    runtime.registry = registry
    name = record['request']['stage']
    from application_service.stage_tasks import blueprint_digest
    if blueprint_digest(name) != record['blueprint_digest']:
        raise ValueError('Worker blueprint differs from the admitting service; align deployments')
    definition = definitions()[name]
    if not persistence.journal_status()['valid']:
        raise ValueError('Journal validation failed')
    if definition['trigger']['poll_kind'] == 'queue':
        # An explicit provider keeps all posting workers in this process, sharing
        # the synchronized client and registries rather than spawning writers.
        return run_queues(persistence, registry, runtime, _get_provider(name),
                          stages=[name], application_ids=record['application_ids'], stage_task_id=record['stage_task_id'], code_revision=revision())
    report = dict(stage_task_id=record['stage_task_id'], started_at=record['started_at'],
                  workload_results=[], code_revision=revision())
    if definition['trigger']['poll_kind'] == 'scheduled':
        admitted = enqueue_scheduled(registry, names=[name])
        ids = {item['workload_id'] for item in admitted if item['status'] == 'queued'}
        if any(item['status'] != 'queued' for item in admitted):
            report['remaining_requirements'] = ['Existing workload is active or failed; reconcile it before requesting another execution.']
    else:
        from .external_event_polling import poll
        report['external_event_polling'] = poll(persistence, registry, stages=[name])
        ids = {item.workload_id for item in registry.pending() if item.stage == name and item.status == 'queued'}
    for workload_id in sorted(ids):
        report['workload_results'].append(runtime.run(workload_id, agent_provider=_get_provider(name)))
    report['finished_at'] = timestamp()
    report['activity_report_pdf'] = publish(persistence, report)
    return report


def consume_once(client, worker_id, executor=execute):
    record = client._request('POST', '/stage-tasks/claim', {'worker_id': worker_id})['task']
    if record is None:
        return False
    path = '/stage-tasks/' + record['stage_task_id'] + '/worker-update'
    token = record['claim_token']
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(20):
            try:
                client._request('POST', path, {'claim_token': token, 'status': 'running'})
            except Exception:
                # Losing status access must never cause an application retry.
                pass
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    result = dict(status='reconciliation_required', summary={'reason': 'Worker interrupted; manual reconciliation required.'})
    try:
        report = executor(record, client)
        results = report.get('workload_results') or []
        failed = any(r.get('status') == 'failed' or r.get('error') for r in results)
        blocked = any(r.get('status') != 'ok' for r in (report.get('external_event_polling') or {}).values())
        blocked = blocked or bool(report.get('remaining_requirements'))
        result = dict(status='failed' if failed or blocked else 'succeeded',
                      report=report['activity_report_pdf'], code_revision=revision(),
                      summary={'executions': len(results), 'failed': failed or blocked})
    except BaseException:
        result = dict(status='reconciliation_required', code_revision=revision(),
                      summary={'reason': 'Worker interrupted or execution failed. Reconcile application and submission state before any new run.'})
        try:
            from .activity_report import publish
            result['report'] = publish(client, dict(stage_task_id=record['stage_task_id'],
                started_at=record['started_at'], remaining_requirements=[result['summary']['reason']]))
        except Exception:
            pass
        raise
    finally:
        stop.set()
        thread.join(timeout=2)
        # If this write fails the durable running claim remains blocked.
        client._request('POST', path, dict(result, claim_token=token))
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    load(require=True)
    client = ApplicationServiceClient()
    worker_id = 'worker-' + uuid.uuid4().hex
    with open(Path(ROOT) / 'config' / 'concurrent-queues.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            consumed = consume_once(client, worker_id)
            if args.once:
                return 0
            if not consumed:
                time.sleep(5)


if __name__ == '__main__':
    raise SystemExit(main())
