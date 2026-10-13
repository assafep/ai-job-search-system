"""Consume both application queues with eight posting workers and serialized storage."""

import argparse
import fcntl
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Lock, current_thread
from time import monotonic

from adapters.llm.codex_provider import provider
from persistence_api.storage_layout import workload_state_path
from .activity_report import publish
from .event_dispatcher import dispatch
from .persistence_client import PersistenceApiClient
from .ready_submission import ReadySubmission
from .report_checkpoint import ReportCheckpoint
from .runtime_config import load, ROOT
from .workflow_runtime import runtime_from_environment
from .workload_registry import registry_from_environment


def run(persistence, registry, runtime, agent_provider, workers=8):
    if workers != 8:
        raise ValueError('this queue runner requires exactly eight posting workers')
    snapshot = persistence.list_applications()
    initial = [a for a in snapshot if a['status'] in {'Bookmarked', 'Ready for Auto-Apply'}]
    # Defensive deduplication: a posting can never be assigned twice.
    initial = list({a['application_id']: a for a in initial}.values())
    run_id = datetime.now(timezone.utc).strftime('queues-%Y%m%dT%H%M%S%fZ')
    report = {'run_id': run_id, 'started_at': datetime.now(timezone.utc).isoformat(),
              'workers': workers, 'initial_bookmarked_applications': [a for a in initial if a['status'] == 'Bookmarked'],
              'initial_ready_applications': [a for a in initial if a['status'] == 'Ready for Auto-Apply'],
              'submission_authorization': 'Ready for Auto-Apply queue membership',
              'workload_results': [], 'posting_results': []}
    state_path = workload_state_path('system-run', run_id + '.json')
    checkpoint = ReportCheckpoint(persistence, state_path, report)
    metrics_lock = Lock()
    print_lock = Lock()
    active, peak = [0], [0]
    threads = set()
    submission = ReadySubmission(persistence, registry)
    posting_locks = {a['application_id']: Lock() for a in initial}

    def emit(value):
        with print_lock:
            print(json.dumps(value, ensure_ascii=False, default=str), flush=True)

    def consume(application):
        app_id = application['application_id']
        posting_started = monotonic()
        with posting_locks[app_id]:
            with metrics_lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                threads.add(current_thread().name)
            results = []
            emit({'activity': 'started', 'thread': current_thread().name, 'company': application['company'],
                  'application_id': app_id, 'queue': application['status']})
            try:
                if application['status'] == 'Bookmarked':
                    envelope = dispatch('application_package_schedule', {'application_id': app_id},
                                        run_id + ':prepare:' + app_id, registry)
                    try:
                        outcome = runtime.run(envelope['workload_id'], agent_provider=agent_provider)
                    except Exception as exc:
                        outcome = {'workflow': 'prepare-bookmarked', 'workload_id': envelope['workload_id'],
                                   'status': 'failed', 'error': str(exc)}
                    results.append(dict(outcome, application_id=app_id, application=application))
                app = persistence.get_application(app_id)
                if app['status'] == 'Ready for Auto-Apply':
                    envelope = dispatch('application_submission_schedule', {'application_id': app_id},
                                        run_id + ':submit:' + app_id, registry)
                    workload_id = envelope['workload_id']
                    registry.claim(workload_id)
                    try:
                        outcome = submission.execute(app_id, workload_id)
                        if outcome['status'] in {'Submitted successfully', 'Already submitted', 'Blocked'}:
                            registry.complete(workload_id)
                        else:
                            registry.wait_for_input(workload_id, '; '.join(outcome.get('blockers') or [outcome['status']]))
                        results.append({'workflow': 'submit-ready', 'workload_id': workload_id,
                                        'application_id': app_id, 'application': application,
                                        'status': registry.get(workload_id).status, 'submission': outcome})
                    except Exception as exc:
                        registry.fail(workload_id, str(exc))
                        results.append({'workflow': 'submit-ready', 'workload_id': workload_id,
                                        'application_id': app_id, 'application': application, 'status': 'failed', 'error': str(exc)})
                final = persistence.get_application(app_id)
                emit({'activity': 'finished', 'thread': current_thread().name, 'company': application['company'],
                      'application_id': app_id, 'status': final['status']})
                return {'application_id': app_id, 'final_state': final['status'], 'workload_results': results,
                        'elapsed_seconds': monotonic() - posting_started}
            except Exception as exc:
                emit({'activity': 'error', 'application_id': app_id, 'error': str(exc)})
                return {'application_id': app_id, 'error': str(exc), 'workload_results': results,
                        'elapsed_seconds': monotonic() - posting_started}
            finally:
                with metrics_lock:
                    active[0] -= 1

    checkpoint.save(force=True)
    emit({'run_id': run_id, 'workers': workers, 'queues': dict(Counter(a['status'] for a in initial))})
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='posting') as pool:
            futures = [pool.submit(consume, app) for app in initial]
            for future in as_completed(futures):
                outcome = future.result()
                report['posting_results'].append({k: v for k, v in outcome.items() if k != 'workload_results'})
                report['workload_results'].extend(outcome['workload_results'])
                checkpoint.save()
    except BaseException:
        checkpoint.save(force=True)
        raise
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    report['peak_active_postings'] = peak[0]
    report['worker_threads_used'] = sorted(threads)
    report['processed_postings'] = len(report['posting_results'])
    report['final_application_counts'] = dict(Counter(a['status'] for a in persistence.list_applications()))
    report['workload_counts'] = dict(Counter(r['workflow'] for r in report['workload_results']))
    report['storage_synchronization'] = persistence.storage_statistics()
    report['checkpoint_uploads'] = checkpoint.uploads + 1
    report['activity_report_pdf'] = publish(persistence, report)
    checkpoint.save(force=True)
    emit({'activity': 'complete', 'processed': report['processed_postings'], 'peak_active_postings': peak[0],
          'storage_synchronization': report['storage_synchronization'], 'activity_report_pdf': report['activity_report_pdf'],
          'final_counts': report['final_application_counts']})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args(argv)
    load(require=True)
    # Prevent two copies of this runner from clobbering the same operational
    # JSON files or executing the same posting. Threads share one registry.
    path = os.path.join(ROOT, 'config', 'concurrent-queues.lock')
    with open(path, 'a') as process_lock:
        try:
            fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another concurrent queue run is already active')
        persistence = PersistenceApiClient()
        if not persistence.journal_status()['valid']:
            raise ValueError('journal validation failed; refusing to start queue workers')
        registry = registry_from_environment()
        runtime = runtime_from_environment(persistence_client=persistence)
        runtime.registry = registry
        run(persistence, registry, runtime, provider)


if __name__ == '__main__':
    main()
