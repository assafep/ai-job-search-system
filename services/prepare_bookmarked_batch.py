"""Explicitly migrate review holds and consume every snapshotted Bookmarked item."""

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone

from adapters.llm.codex_provider import provider
from persistence_api.storage_layout import workload_state_path
from .activity_report import publish
from .application_package import saved_package, promote_saved_package
from .event_dispatcher import dispatch
from .persistence_client import PersistenceApiClient
from .runtime_config import load
from .workflow_runtime import runtime_from_environment
from .workload_registry import registry_from_environment


def run(persistence, registry, runtime, agent_provider, migrate_review_holds=False):
    initial = persistence.list_applications(status='Bookmarked') or []
    run_id = datetime.now(timezone.utc).strftime('preparation-%Y%m%dT%H%M%S%fZ')
    report = {'run_id': run_id, 'started_at': datetime.now(timezone.utc).isoformat(),
              'initial_bookmarked_applications': initial, 'migrations': [], 'migration_errors': [], 'workload_results': []}
    state_path = workload_state_path('prepare-bookmarked', 'batch-runs/' + run_id + '.json')
    def save():
        persistence.store_file(state_path, json.dumps(report, default=str).encode(), 'application/json')
    save()
    print(json.dumps({'run_id': run_id, 'bookmarked_count': len(initial)}), flush=True)
    if migrate_review_holds:
        for app in initial:
            try:
                events = persistence.list_events(app['application_id']) or []
                text = '\n'.join(str(b) for e in events for b in (e.get('data') or {}).get('bullets', []))
                if not re.search(r'human review|human confirmation|human confirms|pending.*review', text, re.I):
                    continue
                artifact = saved_package(persistence, app)
                if not artifact:
                    report['migration_errors'].append({'application_id': app['application_id'], 'reason': 'No successfully saved package; cannot promote.'})
                    continue
                promote_saved_package(persistence, app, artifact)
                report['migrations'].append(app)
                print(json.dumps({'activity': 'review_hold_migrated', 'company': app['company'], 'application_id': app['application_id']}), flush=True)
            except Exception as exc:
                report['migration_errors'].append({'application_id': app['application_id'], 'reason': str(exc)})
    save()
    for app in initial:
        # Fresh explicit-run identity reconsumes every item, including prior
        # succeeded/dead-letter workloads, without resetting their history.
        envelope = dispatch('application_package_schedule', {'application_id': app['application_id']},
                            run_id + ':' + app['application_id'], registry)
        print(json.dumps({'activity': 'preparing', 'company': app['company'], 'application_id': app['application_id']}), flush=True)
        try:
            outcome = runtime.run(envelope['workload_id'], agent_provider=agent_provider)
        except Exception as exc:
            outcome = {'workflow': 'prepare-bookmarked', 'workload_id': envelope['workload_id'],
                       'application_id': app['application_id'], 'status': 'failed', 'error': str(exc)}
        outcome['application_id'] = app['application_id']
        report['workload_results'].append(outcome)
        print(json.dumps({'company': app['company'], 'status': outcome['status'], 'activity': outcome.get('activity'),
                          'error': outcome.get('error')}), flush=True)
        save()
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    report['final_application_counts'] = dict(Counter(a['status'] for a in persistence.list_applications()))
    report['processed_count'] = len(report['workload_results'])
    report['activity_report_pdf'] = publish(persistence, report)
    save()
    print(json.dumps({'activity_report_pdf': report['activity_report_pdf'], 'migrated': len(report['migrations']),
                      'processed': report['processed_count'], 'final_counts': report['final_application_counts']}), flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--migrate-review-holds', action='store_true')
    args = parser.parse_args(argv)
    load(require=True)
    persistence = PersistenceApiClient()
    registry = registry_from_environment()
    runtime = runtime_from_environment(persistence_client=persistence)
    runtime.registry = registry
    run(persistence, registry, runtime, provider, args.migrate_review_holds)


if __name__ == '__main__':
    main()
