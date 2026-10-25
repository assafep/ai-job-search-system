"""One bounded live run of every configured workload and the initial ready queue.

Initial ready IDs are snapshotted before any discovery/preparation. Ready queue
membership authorizes submission for initial and newly prepared items.
Email/calendar polls admit work only when matching messages or events are found.
"""

import argparse
import json
from collections import Counter
from datetime import datetime, timezone

from adapters.llm.codex_provider import provider
from application_service.storage_layout import workload_state_path
from .automation_daemon import AutomationDaemon
from .polling import enqueue_scheduled, maintenance_names
from .application_service_client import ApplicationServiceClient
from .ready_submission import ReadySubmission
from .report_checkpoint import ReportCheckpoint
from .runtime_config import load
from .workflow_runtime import runtime_from_environment
from .workload_registry import registry_from_environment


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


def run(persistence, registry, runtime, agent_provider):
    started = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    initial = persistence.list_applications(status='Prepared') or []
    outcomes = []
    report = {'run_id': run_id, 'started_at': started,
              'initial_ready_applications': initial, 'workload_results': outcomes}
    state_path = workload_state_path('system-run', run_id + '.json')
    checkpoint = ReportCheckpoint(persistence, state_path, report)
    checkpoint.save(force=True)
    emit({'run_id': run_id, 'initial_ready_count': len(initial)})
    daemon = AutomationDaemon(registry, runtime, persistence, agent_provider)
    submissions = ReadySubmission(persistence, registry)
    # Process the original queue first, even when unrelated generation fails.
    for application in initial:
        app_id = application['application_id']
        emit({'workflow': 'submit-prepared', 'application_id': app_id, 'activity': 'starting'})
        try:
            result = submissions.run_one(app_id)
            outcomes.append({'workflow': 'submit-prepared', 'application_id': app_id, 'result': result})
        except Exception as exc:
            outcomes.append({'workflow': 'submit-prepared', 'application_id': app_id, 'error': str(exc)})
        emit(outcomes[-1])
        checkpoint.save()

    scheduled = enqueue_scheduled(registry, names=maintenance_names())
    # Recovery precedes new work; minute-bucket identity is shared with the daemon.
    reconciliation = next(item for item in scheduled if item['workflow'] == 'reconcile')
    if registry.get(reconciliation['workload_id']).status in {'queued', 'failed'}:
        outcomes.append(runtime.run(reconciliation['workload_id'], agent_provider=agent_provider))
    from .external_event_polling import poll
    complete = getattr(persistence, 'complete_application', None)
    report['external_event_polling'] = poll(persistence, registry, complete=complete)
    emit({'activity': 'external_event_polling', 'sources': report['external_event_polling']})
    enqueue_scheduled(registry, names=['discover'], payloads={
        'discover': {'source_names': ['freehire'], 'results_per_page': 20}})

    attempted = set()

    def drain():
        for record in list(registry.pending()):
            if record.workload_id in attempted:
                continue
            attempted.add(record.workload_id)
            emit({'workflow': record.workflow, 'workload_id': record.workload_id, 'activity': 'starting'})
            try:
                result = runtime.run(record.workload_id, agent_provider=agent_provider)
            except Exception as exc:
                result = {'workflow': record.workflow, 'workload_id': record.workload_id,
                          'status': 'failed', 'error': str(exc)}
            outcomes.append(result)
            emit(result)
            checkpoint.save()

    drain()
    while True:
        daemon.enqueue_bookmark_preparation()
        if not any(record.workload_id not in attempted for record in registry.pending()):
            break
        drain()
    daemon.enqueue_ready_submission()
    drain()
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    report['initial_ready_final_states'] = [persistence.get_application(a['application_id']) for a in initial]
    report['initial_ready_processed'] = len([r for r in outcomes if r.get('workflow') == 'submit-prepared'
                                            and r.get('application_id') in {a['application_id'] for a in initial}])
    report['final_application_counts'] = dict(Counter(a['status'] for a in persistence.list_applications()))
    report['workload_counts'] = dict(Counter(r.get('workflow', 'unknown') for r in outcomes))
    report['all_live_integrations_executed'] = all(
        item['status'] == 'ok' and not item.get('truncated') for item in report['external_event_polling'].values())
    report['registry'] = registry.report()
    report['checkpoint_uploads'] = checkpoint.uploads + 1
    if hasattr(persistence, 'storage_statistics'):
        report['storage_synchronization'] = persistence.storage_statistics()
    from .activity_report import publish
    report['activity_report_pdf'] = publish(persistence, report)
    checkpoint.save(force=True)
    emit({'activity_report_pdf': report['activity_report_pdf'],
          'workload_counts': report['workload_counts'], 'final_application_counts': report['final_application_counts']})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args(argv)
    load(require=True)
    persistence = ApplicationServiceClient()
    if not persistence.journal_status()['valid']:
        raise ValueError('journal lifecycle migration/validation must succeed before the live run')
    registry = registry_from_environment()
    runtime = runtime_from_environment(persistence_client=persistence)
    runtime.registry = registry
    run(persistence, registry, runtime, provider)


if __name__ == '__main__':
    main()
