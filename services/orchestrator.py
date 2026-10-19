"""Continuous state queues with live consumer allocation and central routing."""

import argparse
import base64
import hashlib
import json
import os
import re
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Event, Thread, RLock
from time import monotonic

from persistence_api.journal_format import ALL_STATES, normalize_posting_url
from persistence_api.state_machine import validate_transition
from services.activity_log import configure_from_runtime, log_worker
from services.queue_monitor import paint_queues, watch
from . import pipeline_config
from .orchestration_lock import runner_lock
from .persistence_client import PersistenceApiError
from .pipeline_agents import PipelineAgents, plain, read_posting
from .runtime_config import ROOT, load as load_runtime
from .workload_registry import WorkloadRegistry

PREFIX = 'pipeline-'
DEFAULT_STATUS = os.path.join(ROOT, 'config', 'orchestrator-status.json')
HANDLED_STATUSES = {'Applied', 'Interviewing', 'Rejected', 'Offer'}
RETIRE_STATUSES = {'Discovered', 'Bookmarked', 'Validated', 'Prepared', 'Manual Apply Required'}
DUPLICATE_AGENTS = {'read-posting', 'score', 'application'}
BOOKMARK_OUTCOMES = {'accepted', 'read', 'prepared'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def version(application):
    return digest({key: application.get(key) for key in
                   ('application_id', 'status', 'last_event_id', 'company', 'role', 'posting_url')})


def normalized_text(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).casefold()


def posting_identity(application, description=None):
    """Identity used to recognize one posting across boards and domains."""
    description = normalized_text(description)
    if len(description) < 500:
        description = ''
    return {
        'url': normalize_posting_url(application.get('posting_url') or ''),
        'company': normalized_text(application.get('company')),
        'role': normalized_text(application.get('role')),
        'description': description,
    }


QUOTA_MARKERS = ("usage limit", "insufficient_quota")
TRANSIENT_STORAGE_MARKERS = (
    "remote end closed connection",
    "connection reset",
    "connection refused",
    "timed out",
    "timeout",
    "temporarily unavailable",
)
QUOTA_RETRY_RE = re.compile(
    r"try again at ([A-Za-z]+ \d{1,2})(?:st|nd|rd|th)?, (\d{4}) (\d{1,2}:\d{2} [AP]M)",
    re.IGNORECASE)
# Pipelines that call the Codex-backed application provider. Reading and scoring
# use the configured local coding agent and keep running during a Codex outage.
QUOTA_AGENTS = {"application", "correspondence", "interview-prep", "auto-apply"}


def transient_storage_error(error):
    """Return whether a failed artifact save can be retried without a new model call."""
    text = str(error or "").lower()
    return any(marker in text for marker in TRANSIENT_STORAGE_MARKERS)


def provider_quota_until(error, now=None):
    """Return the next retry time for a provider quota failure, or None."""
    text = str(error or "")
    if not any(marker in text.lower() for marker in QUOTA_MARKERS):
        return None
    now = now or datetime.now(timezone.utc)
    match = QUOTA_RETRY_RE.search(text)
    if match:
        parsed = datetime.strptime(
            "{} {} {}".format(match.group(1), match.group(2), match.group(3)),
            "%b %d %Y %I:%M %p")
        local = datetime.now().astimezone().tzinfo
        parsed = parsed.replace(tzinfo=local).astimezone(timezone.utc)
        if parsed > now:
            return parsed
    return now + timedelta(minutes=30)


def duplicate_reason(candidate, handled):
    """Return why two identities are the same posting, or None."""
    if candidate['url'] and candidate['url'] == handled['url']:
        return 'posting URL'
    if (candidate['company'] and candidate['role']
            and (candidate['company'], candidate['role']) == (handled['company'], handled['role'])):
        return 'company and role'
    if candidate['description'] and candidate['description'] == handled['description']:
        return 'posting content'
    return None


def format_queue_counts(counts):
    """One stable line of current queue sizes, including empty queues."""
    counts = counts or {}
    return ' '.join('{}={}'.format(state, int(counts.get(state, 0))) for state in ALL_STATES)


def parse_worker_overrides(items):
    """Parse repeated pipeline=count flags. The config file supplies the rest."""
    overrides = {}
    for item in items or []:
        name, separator, raw = str(item).partition('=')
        name = name.strip()
        if not separator or not name or not raw.strip():
            raise ValueError('worker override must be pipeline=count, for example scout=2')
        try:
            count = int(raw)
        except ValueError:
            raise ValueError('worker count must be an integer: ' + item)
        if not 0 <= count <= 64:
            raise ValueError('workers must be an integer between 0 and 64: ' + name)
        overrides[name] = count
    return overrides


# The application package is the PDF in the position's Drive folder. These
# fields are the letter and resume body, so they stay out of the position journal.
DOCUMENT_BODY_FIELDS = frozenset({
    'cover_letter', 'resume_text', 'resume', 'resume_html', 'cv_html', 'cv_text', 'resume_body',
    'description',
})


def output_bullets(output, prefix=''):
    """Render output fields in the position log as readable notes."""
    if isinstance(output, dict):
        return [text for key, value in output.items()
                for text in output_bullets(value, (prefix + ' / ' if prefix else '') + key.replace('_', ' '))]
    if isinstance(output, list):
        return [text for i, value in enumerate(output) for text in output_bullets(value, prefix + ' ' + str(i + 1))] or [prefix + ': []']
    return [prefix + ': ' + str(output)]


def omit_document_bodies(value):
    """Drop cover-letter and resume text before a journal event is written."""
    if isinstance(value, dict):
        return {key: omit_document_bodies(item) for key, item in value.items() if key not in DOCUMENT_BODY_FIELDS}
    if isinstance(value, list):
        return [omit_document_bodies(item) for item in value]
    return value


def journal_output(agent, output):
    if agent in {'application', 'read-posting'}:
        output = omit_document_bodies(output)
    return output_bullets(output)


class Orchestrator:
    def __init__(self, persistence, registry, agents, config_path=pipeline_config.DEFAULT_PATH,
                 status_path=None, event_poller=None, worker_overrides=None):
        self.persistence, self.registry, self.agents = persistence, registry, agents
        self.config_path, self.status_path = config_path, status_path
        self.event_poller = event_poller
        self.worker_overrides = dict(worker_overrides or {})
        self.config = pipeline_config.load(config_path)
        self._apply_worker_overrides()
        self.config_error = None
        self.pool = ThreadPoolExecutor(max_workers=64, thread_name_prefix='pipeline')
        self.inflight = {}
        self.stop = Event()
        self.monitor = False
        self.last_scan = self.last_event_poll = self.last_discovery = float('-inf')
        self.applications = []
        self.last_error = None
        self.provider_backoff_until = None
        self._description_lock = RLock()
        self._descriptions = {}
        # Recover checkpoints, not side effects. A submission handler reads its
        # durable receipt before any possible external submission retry.
        for record in registry.snapshot():
            if record.workflow.startswith(PREFIX) and record.status == 'running':
                registry.fail(record.workload_id, 'Orchestrator interrupted; resume durable result or receipt')

    def _apply_worker_overrides(self):
        for name, count in self.worker_overrides.items():
            if name == 'discovery':
                self.config['discovery_workers'] = count
                continue
            if name not in self.config['pipelines']:
                raise ValueError('unknown pipeline: ' + name)
            self.config['pipelines'][name]['workers'] = count
        pipeline_config.validate(self.config)

    def reload(self):
        try:
            self.config = pipeline_config.load(self.config_path)
            self._apply_worker_overrides()
            self.config_error = None
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.config_error = str(error)  # Retain the last valid allocation.

    def _acknowledge_origin(self, origin_id):
        """Mark the source email or calendar workload done after its pipeline commits.

        Those workloads stay queued until a pipeline claims them. Completing a
        queued record is rejected, and a dead-lettered source must not undo the
        application result that already committed.
        """
        try:
            origin = self.registry.get(origin_id)
        except KeyError:
            return
        if origin.status in {'succeeded', 'dead_letter'}:
            return
        if origin.status in {'queued', 'failed', 'awaiting_input'}:
            try:
                self.registry.claim(origin_id)
            except ValueError:
                return
            origin = self.registry.get(origin_id)
        if origin.status == 'running':
            self.registry.complete(origin_id)

    def _finish(self, record, checkpoint, application):
        checkpoint['observed_version'] = version(application)
        self.registry.checkpoint_result(record.workload_id, checkpoint)
        origin = record.event['payload'].get('origin_workload_id')
        if origin:
            self._acknowledge_origin(origin)
        self.registry.complete(record.workload_id)

    def _saved_description(self, application):
        app_id = application['application_id']
        observed = version(application)
        with self._description_lock:
            cached = self._descriptions.get(app_id)
            if cached and cached[0] == observed:
                return cached[1]
            try:
                description = read_posting(self.persistence, application).get('description')
            except (ValueError, KeyError, TypeError, PersistenceApiError):
                # A handled posting that can no longer be read, including a 404
                # after its journal tab moved, must not fail the posting being
                # checked. Company, role, and URL matching still run.
                description = None
            self._descriptions[app_id] = (observed, description)
            return description

    def _result_description(self, result):
        for item in (result or {}).get('artifacts') or []:
            name = str(item.get('name') or '')
            if item.get('label') != 'Posting content' and not name.startswith('Job Posting'):
                continue
            try:
                data = json.loads(base64.b64decode(item['content_base64'], validate=True))
            except (KeyError, ValueError, TypeError):
                continue
            if isinstance(data, dict) and data.get('description'):
                return data['description']
        return None

    def _handled_match(self, application, description):
        """Return a handled posting and match reason when this one was already handled."""
        candidate = posting_identity(application, description)
        for other in self.persistence.list_applications() or []:
            other = plain(other)
            if not other or other.get('application_id') == application.get('application_id'):
                continue
            if other.get('status') not in HANDLED_STATUSES:
                continue
            reason = duplicate_reason(candidate, posting_identity(other))
            if not reason and candidate['description']:
                reason = duplicate_reason(candidate, posting_identity(other, self._saved_description(other)))
            if reason:
                return other, reason
        return None

    def _duplicate_result(self, existing, reason):
        return {'outcome': 'duplicate', 'output': {
            'error': 'Posting was already handled ({}; matched by {})'.format(existing.get('status'), reason),
            'match': reason,
            'existing_application_id': existing.get('application_id'),
            'existing_status': existing.get('status'),
            'existing_company': existing.get('company'),
            'existing_role': existing.get('role'),
        }}

    def _retire_duplicates(self, canonical):
        """Move unhandled copies aside after this posting is Applied or Rejected."""
        canonical = plain(canonical)
        identity = posting_identity(canonical, self._saved_description(canonical))
        for other in list(self.persistence.list_applications() or []):
            other = plain(other)
            if not other or other.get('application_id') == canonical.get('application_id'):
                continue
            if other.get('status') not in RETIRE_STATUSES:
                continue
            reason = duplicate_reason(posting_identity(other, self._saved_description(other)), identity)
            if not reason:
                continue
            try:
                self.persistence.transition(other['application_id'], 'Duplicate', {
                    'source': 'orchestrator',
                    'bullets': ['Duplicate of {} / {} ({}); matched by {}.'.format(
                        canonical.get('company'), canonical.get('role'), canonical.get('status'), reason)],
                }, 'duplicate-of-{}-{}'.format(canonical['application_id'], other['application_id']))
            except Exception as error:
                self.last_error = 'Duplicate retirement failed for {}: {}'.format(other.get('application_id'), error)

    def _commit(self, record):
        payload = record.event['payload']
        spec = payload['spec']
        checkpoint = deepcopy(record.result)
        result = checkpoint['value']
        # Older bookmark checkpoints used an undeclared failure outcome. Resume
        # their saved result through the current route without reading the URL again.
        if spec['agent'] == 'read-posting' and result.get('outcome') == 'failed':
            result['outcome'] = 'read_failure'
            self.registry.checkpoint_result(record.workload_id, checkpoint)
        app_id = payload['application_id']
        app = plain(self.persistence.get_application(app_id))
        if app is None:
            raise ValueError('position was deleted before result recording')
        # Scout acceptance and preparation both bookmark or promote. Recheck
        # handled postings immediately before that write, using the fetched text
        # when the URL and company/role did not already match.
        if (spec.get('agent') in DUPLICATE_AGENTS and result.get('outcome') in BOOKMARK_OUTCOMES
                and not checkpoint.get('effects_saved')):
            found = self._handled_match(app, self._result_description(result) or self._saved_description(app))
            if found:
                result = self._duplicate_result(*found)
                checkpoint['value'] = result
        target = spec['routes'][result['outcome']]
        if app['status'] not in spec['states'] and app['status'] != target:
            checkpoint['routing_skipped'] = 'position state changed externally'
        refs = checkpoint.setdefault('artifact_references', [])
        try:
            if not checkpoint.get('effects_saved'):
                for item in result.get('artifacts', []):
                    content = base64.b64decode(item['content_base64'], validate=True)
                    key = 'pipeline-artifact-' + digest([app_id, item['name'], item['content_base64']])
                    artifact = plain(self.persistence.store_artifact(app_id, item['name'], content, item['mime_type'], key))
                    reference = ('https://drive.google.com/file/d/{}/view'.format(artifact['drive_file_id'])
                                 if artifact.get('drive_file_id') else artifact['artifact_id'])
                    note = item['label'] + ': ' + reference
                    if note not in refs:
                        refs.append(note)
                if result.get('research'):
                    research = result['research']
                    self.persistence.write_research(app_id, research['title'], research['content'], record.workload_id + '-research')
                checkpoint['effects_saved'] = True
                self.registry.checkpoint_result(record.workload_id, checkpoint)
        except Exception as error:
            # A dropped connection still has the saved model result. Retry the
            # upload on a later attempt instead of dead-lettering the posting.
            if transient_storage_error(error) and record.attempts < self.registry.max_attempts:
                raise
            # Save the agent output and storage error for central failure routing.
            # No second model invocation is needed to finish this operation.
            checkpoint['value'] = {'outcome': 'system_error', 'output': {
                'agent_output': result['output'], 'error': 'Saving pipeline artifacts failed: ' + str(error)}}
            checkpoint['effects_saved'] = True
            self.registry.checkpoint_result(record.workload_id, checkpoint)
            return self._commit(self.registry.get(record.workload_id))
        bullets = ['Pipeline: ' + payload['pipeline'], 'Agent: ' + spec['agent'],
                   'Outcome: ' + result['outcome']] + journal_output(spec['agent'], result['output']) + refs
        # The agent only returns an outcome. This orchestrator writes the single
        # journal event. A status change is the last bullet of that event.
        app = plain(self.persistence.get_application(app_id))
        if target and app['status'] in spec['states'] and app['status'] != target:
            validate_transition(app['status'], target)
            app = plain(self.persistence.transition(app_id, target, {
                'source': spec['agent'] + ' agent', 'bullets': bullets}, record.workload_id + '-route'))
            if target in {'Applied', 'Rejected'}:
                self._retire_duplicates(app)
        else:
            self.persistence.append_event(app_id, 'pipeline_result', {
                'source': spec['agent'] + ' agent', 'bullets': bullets}, record.workload_id + '-output')
        self._finish(record, checkpoint, app)

    def _remember_worker(self, payload, application):
        payload['worker'] = payload.get('worker') or self._worker_slot(payload['pipeline'])
        return payload['worker'], application.get('status'), application.get('company'), application.get('role')

    def _process(self, workload_id):
        self.registry.claim(workload_id)
        record = self.registry.get(workload_id)
        payload = record.event['payload']
        posting = None
        try:
            if record.result is None:
                app = plain(self.persistence.get_application(payload['application_id']))
                if app is None:
                    raise ValueError('position no longer exists')
                if app['status'] not in payload['spec']['states']:
                    checkpoint = {'value': {'outcome': 'failed', 'output': {'skipped': 'position changed state before claim'}}}
                    self._finish(record, checkpoint, app)
                    return
                posting = self._remember_worker(payload, app)
                log_worker(*posting, 'started')
                try:
                    found = None
                    if payload['spec'].get('agent') in DUPLICATE_AGENTS:
                        found = self._handled_match(app, self._saved_description(app))
                    if found:
                        result = self._duplicate_result(*found)
                    else:
                        result = self.agents(payload['spec'], app, workload_id, payload.get('source_event'))
                    if not isinstance(result, dict) or result.get('outcome') not in payload['spec']['routes'] or 'output' not in result:
                        raise ValueError('pipeline handler must return a declared outcome and output')
                    # Enforce checkpoint serializability before any state routing.
                    json.dumps(result)
                except Exception as error:
                    from .scout_reading import PostingReadFailure
                    quota_until = provider_quota_until(error)
                    if quota_until is not None:
                        # A quota reset is not a defect in the posting. Leave it
                        # in its current state and try again when the provider
                        # is available instead of dead-lettering it.
                        self.provider_backoff_until = quota_until
                        self.last_error = "provider quota exhausted; affected pipelines paused until {}".format(
                            quota_until.isoformat())
                        self.registry.defer(workload_id, self.last_error)
                        return
                    outcome = ('read_failure' if isinstance(error, PostingReadFailure)
                               and payload['spec']['agent'] == 'read-posting' else 'system_error')
                    result = {'outcome': outcome, 'output': {'error': str(error)}}
                self.registry.checkpoint_result(workload_id, {'value': result})
            else:
                app = plain(self.persistence.get_application(payload['application_id']))
                if app is not None:
                    posting = self._remember_worker(payload, app)
                    log_worker(*posting, 'started')
            self._commit(self.registry.get(workload_id))
        except Exception as error:
            self.registry.fail(workload_id, str(error))
            raise
        finally:
            if posting:
                log_worker(posting[0], posting[1], posting[2], posting[3], 'finished')

    def _maybe_discover(self):
        """Search each configured source once per cycle in the discovery pool."""
        interval = self.config.get('discovery_seconds')
        if not interval:
            return
        workers = self.config.get('discovery_workers', 0)
        if not workers:
            return
        if any(item.get('discovery') for item in self.inflight.values()):
            return
        if monotonic() - self.last_discovery < interval:
            return
        self.last_discovery = monotonic()
        sources = self.config.get('discovery_sources') or ['freehire', 'jobicy']
        for index in range(min(workers, len(sources))):
            slot = index + 1
            batch = sources[index::workers]
            future = self.pool.submit(self._run_discovery, slot, batch)
            self.inflight[future] = {
                'application_id': 'discovery-' + str(slot), 'pipeline': 'discovery',
                'workload_id': 'discovery-' + str(slot), 'worker': slot, 'discovery': True,
            }

    def _collect_postings(self, profile, sources=None):
        from services.workflow_runtime import WorkflowRuntime
        job_search = profile.get('job_search') if isinstance(profile, dict) else None
        job_search = job_search or {}
        merged = []
        seen = set()
        errors = []
        terms = list(job_search.get('search_terms') or [])
        if not terms:
            from services.search_planner import derive_search_terms
            terms = derive_search_terms(profile, self.agents.discovery_provider)
        for name in sources or self.config.get('discovery_sources') or ['freehire', 'jobicy']:
            context = {
                'source_names': [name],
                'search_terms': terms,
                'countries': job_search.get('countries') or [],
                'locations': job_search.get('locations') or [],
                'results_per_page': 20,
            }
            try:
                # Terms are already resolved, so a source fetch must not call the model again.
                WorkflowRuntime._enrich_discovery_context(context, profile, None)
            except Exception as error:
                errors.append('{}: {}'.format(name, error))
                continue
            errors.extend('{}: {}'.format(name, error)
                          for error in context.get('discovery_source_errors') or [])
            for posting in context.get('job_postings') or []:
                url = normalize_posting_url(posting.get('posting_url') or '')
                if not url or url in seen:
                    continue
                seen.add(url)
                merged.append(dict(posting, posting_url=posting.get('posting_url') or url))
        if errors and not merged:
            raise RuntimeError('; '.join(errors))
        if errors:
            self.last_error = '; '.join(errors)
        return merged

    def _already_known(self, candidate, description, applications):
        identity = posting_identity({
            'company': candidate.get('company'),
            'role': candidate.get('role'),
            'posting_url': candidate.get('posting_url'),
        }, description)
        for other in applications:
            other = plain(other)
            if not other:
                continue
            if duplicate_reason(identity, posting_identity(other, None)):
                return True
            if identity['description'] and duplicate_reason(
                    identity, posting_identity(other, self._saved_description(other))):
                return True
        return False

    def _run_discovery(self, slot, sources=None):
        from services.workload_executor import execute_persistence_writes, plan_persistence_writes
        log_worker(slot, 'discovery', 'job boards', 'search', 'started')
        try:
            profile = plain(self.persistence.get_profile()) or {}
            postings = self._collect_postings(profile, sources)
            if not postings:
                return
            applications = [plain(app) for app in self.persistence.list_applications() or []]
            for posting in postings:
                posting_url = posting.get('posting_url', '')
                if not posting_url:
                    continue
                company = posting.get('company', '')
                role = posting.get('role', '')
                if self._already_known({'posting_url': posting_url, 'company': company, 'role': role}, '', applications):
                    continue
                log_worker(slot, 'discovery', company or posting_url, role, 'started')
                try:
                    operations = plan_persistence_writes(
                        'discover', {'job_postings': [posting], 'applicant_profile': profile}, {})
                    written = execute_persistence_writes(operations, self.persistence, dry_run=False)
                finally:
                    log_worker(slot, 'discovery', company or posting_url, role, 'finished')
                results = written.get('results') or []
                created = next((plain(item) for item in results
                                if isinstance(plain(item), dict) and plain(item).get('company')), None)
                if created:
                    applications.append(created)
        finally:
            log_worker(slot, 'discovery', 'job boards', 'search', 'finished')

    def _capacity(self, name):
        desired = self.config['pipelines'].get(name, {}).get('workers', 0)
        return desired - sum(1 for value in self.inflight.values() if value['pipeline'] == name)

    def _worker_slot(self, pipeline):
        used = {item.get('worker') for item in self.inflight.values() if item['pipeline'] == pipeline}
        slot = 1
        while slot in used:
            slot += 1
        return slot

    def _quota_blocked(self, name):
        until = self.provider_backoff_until
        if until is None:
            return False
        if datetime.now(timezone.utc) >= until:
            self.provider_backoff_until = None
            return False
        agent = (self.config.get('pipelines') or {}).get(name, {}).get('agent')
        return agent in QUOTA_AGENTS

    def _launch(self, record):
        payload = record.event['payload']
        app_id, name = payload['application_id'], payload['pipeline']
        if self._quota_blocked(name):
            return False
        if self._capacity(name) <= 0 or any(value['application_id'] == app_id for value in self.inflight.values()):
            return False
        slot = self._worker_slot(name)
        payload['worker'] = slot
        future = self.pool.submit(self._process, record.workload_id)
        self.inflight[future] = {'application_id': app_id, 'pipeline': name, 'workload_id': record.workload_id, 'worker': slot}
        return True

    def tick(self):
        # Publish a heartbeat before the journal scan. A cold read of the
        # position journal can take longer than the supervisor's startup wait.
        self.write_status()
        self.reload()
        for future in list(self.inflight):
            if future.done():
                try:
                    future.result()
                except Exception as error:
                    self.last_error = str(error)
                del self.inflight[future]
                self.last_scan = float('-inf')
        records = self.registry.snapshot()
        # Resume a persisted output before considering fresh work for that item.
        pending_ids = set()
        for record in records:
            if not record.workflow.startswith(PREFIX):
                continue
            payload = record.event['payload']
            if record.status in {'queued', 'failed', 'running', 'dead_letter', 'awaiting_input'}:
                pending_ids.add(payload['application_id'])
            if record.status in {'queued', 'failed'} and record.attempts < self.registry.max_attempts:
                if not self._quota_blocked(payload.get('pipeline')):
                    self._launch(record)
        enabled = {name: spec for name, spec in self.config['pipelines'].items() if spec['workers']}
        if enabled and monotonic() - self.last_scan >= self.config['scan_seconds']:
            self.applications = [plain(app) for app in self.persistence.list_applications()]
            self.last_scan = monotonic()
        if self.event_poller and any(spec.get('event_type') for spec in enabled.values()) and monotonic() - self.last_event_poll >= self.config['event_poll_seconds']:
            self.event_poller(self.persistence, self.registry)
            self.last_event_poll = monotonic()
            records = self.registry.snapshot()
        self._maybe_discover()
        by_id = {record.workload_id: record for record in records}
        for name, spec in enabled.items():
            if self._quota_blocked(name):
                continue
            for app in self.applications:
                app_id = app['application_id']
                if self._capacity(name) <= 0:
                    break
                if app['status'] not in spec['states'] or app_id in pending_ids or any(value['application_id'] == app_id for value in self.inflight.values()):
                    continue
                if spec.get('event_type'):
                    sources = [record for record in records if not record.workflow.startswith(PREFIX)
                               and record.status in {'queued', 'failed'}
                               and record.event.get('event_type') == spec['event_type']
                               and (record.event.get('payload') or {}).get('application_id') == app_id]
                else:
                    sources = [None]
                    if any(record.workflow == PREFIX + name and record.status == 'succeeded'
                           and record.event['payload']['application_id'] == app_id
                           and (record.result or {}).get('observed_version') == version(app) for record in records):
                        continue
                for source in sources[:1]:
                    admission = source.event['event_id'] if source else version(app)
                    workload_id = PREFIX + digest([name, spec['agent'], app_id, admission])
                    if workload_id in by_id:
                        continue
                    payload = {'pipeline': name, 'spec': deepcopy(spec), 'application_id': app_id,
                               'input_version': version(app), 'source_event': source.event if source else None,
                               'origin_workload_id': source.workload_id if source else None}
                    record = self.registry.enqueue({'workload_id': workload_id, 'workflow': PREFIX + name,
                                                    'event': {'event_id': workload_id, 'event_type': 'pipeline_item', 'payload': payload}})
                    if self._launch(record):
                        pending_ids.add(app_id)
        self.write_status()

    def write_status(self):
        value = {'pid': os.getpid(), 'updated_at': datetime.now(timezone.utc).isoformat(),
                 'discovery': {'workers': self.config.get('discovery_workers', 0),
                               'active': sum(1 for item in self.inflight.values() if item.get('discovery'))},
                 'config_error': self.config_error, 'last_error': self.last_error, 'stopping': self.stop.is_set(),
                 'pipelines': {name: {'workers': spec['workers'], 'agent': spec['agent'], 'states': spec['states'],
                                    'active': sum(1 for item in self.inflight.values() if item['pipeline'] == name)}
                               for name, spec in self.config['pipelines'].items()},
                 'inflight': list(self.inflight.values())}
        if self.status_path:
            pipeline_config.atomic_json(self.status_path, value)
        return value

    def close(self):
        self.stop.set()
        self.pool.shutdown(wait=True)
        self.inflight.clear()
        self.write_status()

    def print_queues(self):
        try:
            frame = paint_queues(self.persistence.queue_counts())
        except Exception as error:
            frame = paint_queues({}, error=str(error))
        sys.stdout.write(frame)
        sys.stdout.flush()

    def _monitor(self):
        sys.stdout.write('\033[?1049h\033[?25l')
        sys.stdout.flush()
        try:
            while not self.stop.is_set():
                self.print_queues()
                self.stop.wait(1)
        finally:
            sys.stdout.write('\033[?25h\033[?1049l')
            sys.stdout.flush()

    def serve(self):
        monitor = Thread(target=self._monitor, daemon=True) if self.monitor else None
        if monitor:
            monitor.start()
        try:
            self.write_status()
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception as error:
                    self.last_error = str(error)
                    self.write_status()
                self.stop.wait(self.config['poll_seconds'])
        finally:
            self.close()
            if monitor:
                monitor.join(timeout=2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=pipeline_config.DEFAULT_PATH)
    parser.add_argument('--status-file', default=DEFAULT_STATUS)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    run.add_argument('--monitor', action='store_true', help='Redraw queue counts on stdout every second')
    run.add_argument('--workers', action='append', default=[], metavar='PIPELINE=COUNT',
                     help='Override one pipeline worker count from the config file. Repeat for each pipeline.')
    commands.add_parser('monitor', help='Print queue counts from the persistence API every second')
    commands.add_parser('status')
    commands.add_parser('validate')
    scaling = commands.add_parser('scale')
    scaling.add_argument('pipeline')
    scaling.add_argument('workers', type=int)
    args = parser.parse_args(argv)
    if args.command == 'validate':
        print(json.dumps(pipeline_config.load(args.config), indent=2))
        return 0
    if args.command == 'scale':
        config = pipeline_config.scale(args.config, args.pipeline, args.workers)
        print(json.dumps({'pipeline': args.pipeline, 'workers': config['pipelines'][args.pipeline]['workers']}))
        return 0
    if args.command == 'status':
        with open(args.status_file) as handle:
            status = json.load(handle)
        try:
            os.kill(status['pid'], 0)
            status['running'] = not status.get('stopping')
        except ProcessLookupError:
            status['running'] = False
        print(json.dumps(status, indent=2))
        return 0
    runtime = load_runtime(require=True)
    configure_from_runtime(runtime)
    from .persistence_client import PersistenceApiClient
    if args.command == 'monitor':
        persistence = PersistenceApiClient()
        try:
            watch(persistence.queue_counts)
        except KeyboardInterrupt:
            sys.stdout.write('\n')
        return 0
    overrides = parse_worker_overrides(getattr(args, 'workers', None))
    run_pipelines(args.config, args.status_file, overrides, args.monitor)
    return 0


def run_pipelines(config_path=None, status_file=None, worker_overrides=None, monitor=False):
    """Run every pipeline until SIGINT or SIGTERM. The caller owns logging and the API."""
    from .persistence_client import PersistenceApiClient
    from .worker_main import load_discovery_provider, load_provider
    from .external_event_polling import poll
    config_path = config_path or pipeline_config.DEFAULT_PATH
    with runner_lock():
        persistence = PersistenceApiClient()
        if not persistence.journal_status()['valid']:
            raise ValueError('journal is invalid')
        registry = WorkloadRegistry(
            storage_path=os.environ.get('WORKLOAD_REGISTRY_PATH') or os.path.join(ROOT, 'config', 'workloads.json'))
        agents = PipelineAgents(persistence, registry, load_provider(), discovery_provider=load_discovery_provider())
        service = Orchestrator(
            persistence, registry, agents, config_path, status_file, poll, worker_overrides)
        service.monitor = bool(monitor)
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_args: service.stop.set())
        if not service.monitor:
            print('Orchestrator running; edit pipelines.json or use the scale command.', flush=True)
        service.serve()


if __name__ == '__main__':
    raise SystemExit(main())
