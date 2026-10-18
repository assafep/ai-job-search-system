"""Replay only positions in the operator Replay tab through scout/bookmark."""

import argparse
import base64
import json
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from threading import Lock
from time import monotonic

from adapters.job_boards.posting_details import parse_saved_posting
from .agent_runner import run as run_agent
from .event_dispatcher import dispatch
from .persistence_client import PersistenceApiClient
from .runtime_config import load
from .workflow_runtime import runtime_from_environment
from .workload_effort import effort_for_workflow
from .scout_reading import PostingReadFailure, posting_from_model
from .worker_main import load_discovery_provider
from .workload_executor import plan_persistence_writes
from .orchestration_lock import runner_lock


def replay_positions(persistence, application_id=None):
    tabs = persistence._request('GET', '/journal/tabs')
    parents = {tab['tab_id'] for tab in tabs if tab['title'].strip().lower() == 'replay'}
    if len(parents) != 1:
        raise ValueError('expected one Replay tab')
    positions = [tab for tab in tabs if tab.get('parent_tab_id') in parents]
    if application_id:
        positions = [tab for tab in positions if tab['tab_id'] == application_id]
        if not positions:
            raise ValueError('requested position is not under Replay')
    if not positions:
        raise ValueError('Replay contains no positions')
    return positions


def _run_positions(persistence, runtime, agent_provider, positions, profile, progress):
    outcomes = []
    for tab in positions:
        progress(tab, 'reading and evaluating posting')
        phase = monotonic()
        identity = 'scout-replay-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        outcome = {'application_id': tab['tab_id'], 'title': tab['title'], 'timings': {}}
        try:
            urls = [paragraph['text'].split('Posting URL: ', 1)[1].strip()
                    for paragraph in tab['paragraphs'] if paragraph['text'].startswith('Posting URL: ')]
            if len(set(urls)) != 1 or ' / ' not in tab['title']:
                raise ValueError('Replay position needs one posting URL and a Company / Role title')
            company, role = tab['title'].split(' / ', 1)
            context = {
                'read_posting_url': urls[0],
                'company': company,
                'role': role,
                'job_postings': [{'company': company, 'role': role, 'posting_url': urls[0]}],
                'applicant_profile': profile,
            }
            phase = monotonic()
            selected_provider = (agent_provider.with_reasoning_effort(effort_for_workflow('discover-and-bookmark'))
                                 if hasattr(agent_provider, 'with_reasoning_effort') else agent_provider)
            decision = run_agent('job-discovery', context, selected_provider)
            outcome['timings']['scout_seconds'] = round(monotonic() - phase, 3)
            posting = posting_from_model(urls[0], company, role, decision)
            if posting is None:
                outcome['status'] = 'declined'
            else:
                source = posting
                context['job_postings'] = [source]
                plan_persistence_writes('discover-and-bookmark', context, {'job-discovery': decision})
                phase = monotonic()
                progress(tab, 'saving bookmark and posting content')
                persistence._request('POST', '/journal/tabs/{}/bookmark'.format(tab['tab_id']),
                                     {'idempotency_key': identity + '-bookmark'})
                envelope = dispatch('job_discovery_bookmark_schedule', context, identity, runtime.registry)
                result = runtime.run(envelope['workload_id'], agent_results={'job-discovery': decision})
                artifacts = result['execution']['execution']['results']
                artifact = next(item for item in artifacts if isinstance(item, dict) and item.get('artifact_id'))
                outcome['timings']['bookmark_seconds'] = round(monotonic() - phase, 3)
                phase = monotonic()
                progress(tab, 'verifying saved posting content')
                saved = persistence.read_artifact(tab['tab_id'], artifact['artifact_id'])
                raw = saved.get('content')
                if raw is None:
                    raw = base64.b64decode(saved['content_base64'])
                content = parse_saved_posting(raw)
                if content['description'] != source['description'] or content['posting_url'] != urls[0]:
                    raise ValueError('stored posting differs from the scout source')
                outcome['timings']['verification_seconds'] = round(monotonic() - phase, 3)
                outcome.update(status='bookmarked', workload_id=envelope['workload_id'],
                               drive_file_id=artifact.get('drive_file_id'))
        except Exception as error:
            progress(tab, 'recording failure')
            outcome.update(status='failed', error=str(error))
            target = 'Manual Apply Required' if isinstance(error, PostingReadFailure) else 'Dead Letters Queue'
            try:
                application = persistence.get_application(tab['tab_id'])
                if application and application['status'] in {'Replay', 'Bookmarked'}:
                    persistence.transition(tab['tab_id'], target, {
                        'source': 'job-discovery agent',
                        'bullets': ['Scout replay failed: ' + str(error)],
                    }, identity + '-failure-' + tab['tab_id'])
                    outcome['routed_to'] = target
            except Exception as routing_error:
                outcome['routing_error'] = str(routing_error)
        outcomes.append(outcome)
        progress(tab, outcome.get('routed_to') or outcome['status'])
        print(json.dumps(outcome, ensure_ascii=False), flush=True)
    return outcomes


def run(persistence, runtime, agent_provider, application_id=None, workers=1, progress_seconds=10):
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 64:
        raise ValueError('workers must be between 1 and 64')
    if progress_seconds <= 0:
        raise ValueError('progress_seconds must be positive')
    started = monotonic()
    positions = replay_positions(persistence, application_id)
    if len({tab['tab_id'] for tab in positions}) != len(positions):
        raise ValueError('Replay contains duplicate position IDs')
    profile = persistence.get_profile()
    lock = Lock()
    states = {index + 1: {'title': positions[index]['title'] if index < len(positions) else 'No posting',
                          'phase': 'queued' if index < len(positions) else 'idle'} for index in range(workers)}

    def snapshot():
        with lock:
            print('Replay progress — {:.0f}s elapsed'.format(monotonic() - started), flush=True)
            for worker, state in sorted(states.items()):
                print('Worker {}: {} — {}'.format(worker, state['title'], state['phase']), flush=True)

    def process(worker, assigned):
        def progress(tab, phase):
            with lock:
                states[worker] = {'title': tab['title'], 'phase': phase}
        results = _run_positions(persistence, runtime, agent_provider, assigned, profile, progress)
        for item in results:
            item['worker'] = worker
        return results

    snapshot()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='replay') as pool:
        futures = [pool.submit(process, worker + 1, positions[worker::workers]) for worker in range(workers)]
        pending = set(futures)
        next_report = monotonic() + progress_seconds
        while pending:
            _, pending = wait(pending, timeout=max(0, next_report - monotonic()))
            if monotonic() >= next_report:
                snapshot()
                next_report += progress_seconds
        outcomes = [item for future in futures for item in future.result()]
    by_id = {item['application_id']: item for item in outcomes}
    outcomes = [by_id[tab['tab_id']] for tab in positions]
    snapshot()
    print(json.dumps({'total_seconds': round(monotonic() - started, 3), 'positions': len(outcomes)}), flush=True)
    return outcomes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--application-id', help='Restrict replay to this child of Replay')
    parser.add_argument('--workers', type=int, default=1, help='Concurrent Replay workers (1–64)')
    parser.add_argument('--progress-seconds', type=float, default=10, help='Seconds between worker progress snapshots')
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 64 or args.progress_seconds <= 0:
        parser.error('workers must be 1–64 and progress-seconds must be positive')
    load(require=True)
    with runner_lock():
        persistence = PersistenceApiClient()
        if not persistence.journal_status().get('valid'):
            raise ValueError('journal validation failed; refusing to start Replay workers')
        runtime = runtime_from_environment(persistence_client=persistence)
        started_at = datetime.now(timezone.utc).isoformat()
        outcomes = run(persistence, runtime, load_discovery_provider(), args.application_id,
                       args.workers, args.progress_seconds)
        from .activity_report import publish
        report = {'run_id': 'replay-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'),
                  'started_at': started_at, 'finished_at': datetime.now(timezone.utc).isoformat(),
                  'workers': args.workers, 'storage_synchronization': persistence.storage_statistics(),
                  'workload_results': [dict(item, workflow='discover-and-bookmark',
                                            activity='Worker {}: {} — {}'.format(item['worker'], item['title'],
                                                                               item.get('routed_to') or item['status']))
                                       for item in outcomes],
                  'final_application_counts': persistence.queue_counts()}
        saved = publish(persistence, report)
        print('Activity report: ' + saved['path'], flush=True)
        if saved.get('file_id'):
            print('Activity report link: https://drive.google.com/file/d/{}/view'.format(saved['file_id']), flush=True)
    return 1 if any(item['status'] == 'failed' for item in outcomes) else 0


if __name__ == '__main__':
    raise SystemExit(main())
