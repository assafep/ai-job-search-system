"""Pipeline work returns outputs and artifacts, never lifecycle transitions.

Only the orchestrator knows routing policy. Stateless LLMs receive plain
context. This runtime boundary reads persistence and invokes external adapters.
"""

import base64
import re
from dataclasses import asdict, is_dataclass

from pypdf import PdfReader
from adapters.job_boards.posting_details import posting_artifact_name, posting_content_bytes, parse_saved_posting
from .scout_reading import posting_from_model
from persistence_api.journal_format import normalize_posting_url
from persistence_api.profile import calendly_url
from .agent_runner import run as run_agent
from .application_package import saved_package
from .document_generator import generate_application_pdf
from .ready_submission import ReadySubmission
from .workload_effort import effort_for_workflow


def plain(value):
    return asdict(value) if is_dataclass(value) else value


def artifact(name, content, mime_type, label):
    return {'name': name, 'content_base64': base64.b64encode(content).decode('ascii'),
            'mime_type': mime_type, 'label': label}


def read_posting(persistence, application):
    app_id = application['application_id']
    artifacts = [plain(item) for item in persistence.list_artifacts(app_id)]
    postings = [a for a in artifacts if a.get('name', '').startswith('Job Posting - ')]
    if not postings:
        raise ValueError('saved posting content is missing')
    # A folder listing is not chronological. Prefer the latest explicit
    # posting-content event instead of choosing the last file returned by Drive.
    selected = None
    for event in reversed(persistence.list_events(app_id)):
        for bullet in (plain(event).get('data') or {}).get('bullets', []):
            if bullet.startswith('Posting content:'):
                selected = next((a for a in postings if (a.get('drive_file_id') or a['artifact_id']) in bullet), None)
                if selected:
                    break
        if selected:
            break
    if selected is None and len(postings) != 1:
        raise ValueError('multiple saved postings without an authoritative content link')
    selected = selected or postings[0]
    raw = persistence.read_artifact(app_id, selected['artifact_id'])
    content = raw.get('content')
    if content is None:
        content = base64.b64decode(raw['content_base64'], validate=True)
    posting = parse_saved_posting(content)
    if normalize_posting_url(posting['posting_url']) != normalize_posting_url(application['posting_url']):
        raise ValueError('saved posting content belongs to another position')
    return posting


class PipelineAgents:
    def __init__(self, persistence, registry, provider, submission_adapter=None, discovery_provider=None):
        self.persistence = persistence
        self.registry = registry
        self.provider = provider
        self.discovery_provider = discovery_provider or provider
        self.submission = ReadySubmission(persistence, registry, submission_adapter, manage_state=False)

    def invoke(self, name, context, workflow):
        provider = self.discovery_provider if name == 'job-discovery' else self.provider
        if hasattr(provider, 'with_reasoning_effort'):
            provider = provider.with_reasoning_effort(effort_for_workflow(workflow))
        return run_agent(name, context, provider)

    def __call__(self, spec, application, workload_id, event=None):
        name = spec['agent']
        if name == 'auto-apply':
            output = self.submission.execute(application['application_id'], workload_id)
            status = output['status']
            if status in {'Submitted successfully', 'Already submitted'}:
                outcome = 'submitted'
            elif output.get('approval_pending'):
                outcome = 'waiting'
            elif status == 'Submission unconfirmed':
                outcome = 'unconfirmed'
            elif output.get('failure_kind') == 'system_error':
                outcome = 'system_error'
            else:
                outcome = 'failed'
            return {'outcome': outcome, 'output': output}
        profile = self.persistence.get_profile()
        context = {'application': application, 'application_id': application['application_id'],
                   'applicant_profile': profile, 'calendly_url': calendly_url(profile)}
        if event:
            context.update(event.get('payload') or {})
            context['application'] = application
            context['application_id'] = application['application_id']
        if name == 'job-discovery':
            # The model opens the posting URL. Scout does not download it.
            context['read_posting_url'] = application['posting_url']
            context['company'] = application['company']
            context['role'] = application['role']
            context['job_postings'] = [{
                'company': application['company'],
                'role': application['role'],
                'posting_url': application['posting_url'],
            }]
            output = self.invoke(name, context, 'discover-and-bookmark')
            posting = posting_from_model(application['posting_url'], application['company'], application['role'], output)
            if posting is None:
                return {'outcome': 'declined', 'output': output}
            return {'outcome': 'accepted', 'output': output, 'artifacts': [artifact(
                posting_artifact_name(application['company'], application['role']),
                posting_content_bytes(posting), 'application/json', 'Posting content')]}
        if name == 'application':
            existing = saved_package(self.persistence, application)
            if existing:
                return {'outcome': 'prepared', 'output': {'reused_package': existing['reference']}}
            context['job_posting'] = read_posting(self.persistence, application)
            path = (profile.get('source_resume') or {}).get('path')
            if not path:
                raise ValueError('original onboarding resume PDF is required')
            context['resume_text'] = '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)
            output = self.invoke(name, context, 'prepare-bookmarked')
            if not str(output.get('cover_letter') or '').strip():
                return {'outcome': 'failed', 'output': output}
            with open(path, 'rb') as handle:
                resume = handle.read()
            content = generate_application_pdf(output['cover_letter'], calendly_url=context['calendly_url'],
                                               resume_pdf=resume, applicant_profile=profile, application=application)
            personal = profile.get('personal') or {}
            filename = '{} Cover Letter & Resume - {} - {}.pdf'.format(
                personal.get('name') or personal.get('full_name') or 'Applicant', application['company'], application['role'])
            filename = re.sub(r'[\\/:*?"<>|]+', '-', filename)
            return {'outcome': 'prepared', 'output': output,
                    'artifacts': [artifact(filename, content, 'application/pdf', 'Application package')]}
        context['application_events'] = self.persistence.list_events(application['application_id'])
        if name == 'interview-prep':
            context['existing_research'] = self.persistence.list_research(application['application_id'])
            output = self.invoke(name, context, 'prepare-interview')
            return {'outcome': 'prepared', 'output': output,
                    'research': {'title': 'Research — Interview Briefing', 'content': output['briefing']}}
        output = self.invoke(name, context, 'handle-incoming-email')
        intent = output['intent'].strip().lower()
        from .workload_executor import SCHEDULING_INTENTS
        from .document_generator import ensure_scheduling_link
        if intent in SCHEDULING_INTENTS:
            output['draft'] = ensure_scheduling_link(output.get('draft') or 'Please choose a convenient time.', context['calendly_url'])
        outcome = ('interview' if intent in SCHEDULING_INTENTS else
                   'rejected' if intent in {'rejection', 'rejected'} else
                   'offered' if intent == 'offer' else 'processed')
        # A correspondence result proposes a draft. Sending remains a separate
        # approved external action, never an implicit effect of classification.
        return {'outcome': outcome, 'output': output}
