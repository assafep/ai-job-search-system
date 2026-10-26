"""Consume Prepared, one item at a time, without regenerating packages."""

import argparse
import base64
import hashlib
import json
from io import BytesIO

from pypdf import PdfReader

from adapters.browser.submission import adapter_from_environment
from .event_attempt import with_attempt
from .event_dispatcher import dispatch
from .application_package import cover_matches_application
from .application_service_client import ApplicationServiceClient, ApplicationServiceError
from .runtime_config import load
from .workload_effort import effort_for_stage
from .workload_registry import registry_from_environment


OUTCOMES = {'Submitted successfully', 'Already submitted', 'Prepared but not submitted',
            'Submission unconfirmed', 'Blocked'}
SUBMISSION_MODEL = "grok-4.7"


def describe_attempt(receipt, effort, prior_logic):
    """Record the model, effort, logic, obstacles, and what was done about them."""
    attempt = dict(receipt.get("attempt") or {})
    attempt["model"] = attempt.get("model") or SUBMISSION_MODEL
    attempt["effort"] = attempt.get("effort") or effort
    logic = list(prior_logic)
    for step in attempt.get("logic") or []:
        if step not in logic:
            logic.append(step)
    attempt["logic"] = logic
    if not attempt.get("obstacles"):
        attempt["obstacles"] = list(receipt.get("blockers") or [])
    attempt.setdefault("tackled", [])
    return attempt


def attempt_lines(attempt):
    from .event_attempt import attempt_lines as render
    return render(attempt)


class ReadySubmission:
    def __init__(self, persistence, registry, adapter=None, manage_state=True):
        self.persistence = persistence
        self.registry = registry
        self.adapter = adapter or adapter_from_environment()
        self.manage_state = manage_state

    @staticmethod
    def receipt_path(application_id):
        from application_service.storage_layout import workload_state_path
        return workload_state_path('submit-prepared', 'submission-receipts/{}.json'.format(application_id))

    def _receipt(self, application_id):
        try:
            result = self.persistence.read_file(self.receipt_path(application_id))
        except (KeyError, ApplicationServiceError) as exc:
            if isinstance(exc, ApplicationServiceError) and 'returned 404:' not in str(exc):
                raise
            return None
        if not result:
            return None
        data = result.get('content')
        if data is None:
            data = base64.b64decode(result['content_base64'])
        if isinstance(data, str):
            data = data.encode('utf-8')
        if not data or not data.strip():
            return self._unreadable_receipt(application_id)
        try:
            receipt = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return self._unreadable_receipt(application_id)
        if not isinstance(receipt, dict):
            return self._unreadable_receipt(application_id)
        return receipt

    @staticmethod
    def _unreadable_receipt(application_id):
        """An empty receipt file means a submission may already have started."""
        return {
            'status': 'Submission unconfirmed',
            'application_id': application_id,
            'blockers': ['Submission receipt was empty or unreadable. Verify whether the application was received before applying again.'],
        }

    def _record_confirmed(self, application_id, receipt):
        confirmation = receipt.get('confirmation_text') or receipt.get('application_reference')
        if not confirmation:
            raise ValueError('a successful submission requires observed confirmation evidence')
        if not self.manage_state:
            return
        attempt = receipt.get("attempt") or describe_attempt(receipt, "medium", [])
        application = self.persistence.get_application(application_id)
        self.persistence.append_event(application_id, 'application_submitted', {
            'bullets': with_attempt(
                ['Application submitted successfully.', 'Confirmation: ' + str(confirmation)],
                attempt),
            'attempt': attempt,
            'source': 'submission stage',
        }, 'submission-confirmed-' + application_id)
        if application['status'] in {'Prepared', 'Manual Apply Required'}:
            self.persistence.transition(application_id, 'Applied', {
                'source': 'verified external submission',
            }, 'submission-applied-' + application_id)

    def _record_manual_required(self, application_id, receipt):
        if not self.manage_state:
            return
        if receipt.get('status') in {'Submitted successfully', 'Already submitted'}:
            return
        application = self.persistence.get_application(application_id)
        target = ('Dead Letters Queue' if receipt.get('failure_kind') == 'system_error'
                  and receipt.get('status') != 'Submission unconfirmed' else 'Manual Apply Required')
        if application['status'] in {'Prepared', target}:
            attempt = receipt.get("attempt") or describe_attempt(receipt, "medium", [])
            self.persistence.transition(application_id, target, {
                'source': 'auto-apply agent',
                'attempt': attempt,
                'bullets': with_attempt(
                    ['Auto-apply failed: ' + '; '.join(receipt.get('blockers') or [receipt.get('status', 'Unknown failure')])]
                    + (['Submission outcome is unconfirmed. Verify whether it was received before applying again.']
                       if receipt.get('status') == 'Submission unconfirmed' else []),
                    attempt),
            }, 'submission-failure-{}-{}'.format(target, application_id))

    def execute(self, application_id, workload_id):
        try:
            result = self._execute(application_id, workload_id)
            self._record_manual_required(application_id, result)
            return result
        except Exception as exc:
            app = self.persistence.get_application(application_id)
            if app is not None and app['status'] == 'Prepared':
                receipt = self._receipt(application_id) or {'status': 'Blocked', 'pre_submission_failure': True}
                receipt = dict(receipt, application_id=application_id,
                               blockers=['Auto-apply error: ' + str(exc)])
                if receipt['status'] == 'Blocked':
                    receipt['failure_kind'] = 'system_error'
                receipt["attempt"] = describe_attempt(
                    receipt, effort_for_stage("submit-prepared"),
                    ["Started auto-submit for a Prepared application."])
                self.persistence.store_file(self.receipt_path(application_id), json.dumps(receipt).encode(), 'application/json')
                self._record_manual_required(application_id, receipt)
                return receipt
            raise

    def _execute(self, application_id, workload_id):
        app = self.persistence.get_application(application_id)
        if app is None:
            raise KeyError(application_id)
        receipt = self.reusable_receipt(application_id)
        if receipt:
            if receipt['status'] in {'Submitted successfully', 'Already submitted'}:
                self._record_confirmed(application_id, receipt)
            return dict(receipt, replayed=True)
        context = self.prepared_context(application_id)
        artifact = context['package']
        digest = context['package_sha256']
        effort = context['reasoning_effort']
        prior_logic = ['Checked that one combined PDF exists, every page has text, and the cover letter matches the employer and role.']
        # Durably mark the attempt before transmission. An interrupted adapter
        # must never cause an automatic duplicate application.
        pending = {'status': 'Submission unconfirmed', 'application_id': application_id,
                   'package_sha256': digest, 'blockers': ['Submission attempt started; no receipt observed yet.']}
        self.persistence.store_file(self.receipt_path(application_id), json.dumps(pending).encode(), 'application/json')
        result = self.adapter(context)
        if not isinstance(result, dict) or result.get('status') not in OUTCOMES:
            raise ValueError('submission adapter returned an invalid outcome; attempt remains unconfirmed')
        if result['status'] in {'Submitted successfully', 'Already submitted'}:
            if result.get('requires_human_verification'):
                raise ValueError('successful submission cannot also require human verification')
            if not (result.get('confirmation_text') or result.get('application_reference')):
                raise ValueError('submission adapter supplied no confirmation; attempt remains unconfirmed')
        receipt = dict(result, application_id=application_id, package_sha256=digest,
                       package_name=artifact['name'], original_posting_url=app['posting_url'])
        receipt["attempt"] = describe_attempt(receipt, effort, prior_logic)
        self.persistence.store_file(self.receipt_path(application_id), json.dumps(receipt).encode(), 'application/json')
        if result['status'] in {'Submitted successfully', 'Already submitted'}:
            self._record_confirmed(application_id, receipt)
        return receipt

    def reusable_receipt(self, application_id):
        """Return a definitive/uncertain prior outcome; never restart uncertainty."""
        receipt = self._receipt(application_id)
        if not receipt:
            return None
        if receipt['status'] in {'Submitted successfully', 'Already submitted'}:
            return receipt
        if receipt['status'] == 'Submission unconfirmed':
            return dict(receipt, blockers=receipt.get('blockers') or [
                'Previous submission is unconfirmed; verify its outcome before attempting another submission.'])
        if receipt.get('approval_pending') or (
                receipt.get('pre_submission_failure') is True and receipt.get('status') == 'Blocked'
                and receipt.get('failure_kind') == 'system_error' and not receipt.get('package_sha256')):
            return None
        if any(needle in str(item) for item in (receipt.get('blockers') or []) for needle in (
                'No verified employer application destination', 'XAI_API_KEY is not configured')):
            return None
        return receipt

    def prepared_context(self, application_id):
        app = self.persistence.get_application(application_id)
        if app is None:
            raise KeyError(application_id)
        if app['status'] != 'Prepared':
            raise ValueError('submission requires Prepared; found ' + app['status'])
        artifacts = self.persistence.list_artifacts(application_id) or []
        packages = [a for a in artifacts if ' Cover Letter & Resume - ' in a.get('name', '')
                    and a.get('name', '').lower().endswith('.pdf')]
        if len(packages) != 1:
            raise ValueError('expected exactly one combined cover-letter/resume PDF')
        artifact = packages[0]
        raw = self.persistence.read_artifact(application_id, artifact['artifact_id'])
        content = raw.get('content')
        if content is None:
            content = base64.b64decode(raw['content_base64'], validate=True)
        pdf = PdfReader(BytesIO(content))
        if len(pdf.pages) < 2 or not all((page.extract_text() or '').strip() for page in pdf.pages):
            raise ValueError('application package has missing or blank pages')
        cover = pdf.pages[0].extract_text() or ''
        if not cover_matches_application(cover, app):
            raise ValueError('cover letter does not match the employer and role')
        digest = hashlib.sha256(content).hexdigest()
        effort = effort_for_stage('submit-prepared')
        prior_logic = [
            "Checked that one combined PDF exists, every page has text, and the cover letter matches the employer and role.",
        ]
        return {
            'application': app, 'applicant_profile': self.persistence.get_profile(),
            'package': artifact, 'package_bytes': content, 'package_sha256': digest,
            'reasoning_effort': effort,
            'submission_authorized_by_queue': True,
        }

    def run_one(self, application_id):
        envelope = dispatch('application_submission_schedule', {'application_id': application_id},
                            event_id='submit-' + application_id, registry=self.registry)
        workload_id = envelope['workload_id']
        record = self.registry.get(workload_id)
        if record.status == 'succeeded':
            return dict(self._receipt(application_id) or {'status': 'Already processed'}, replayed=True)
        self.registry.claim(workload_id)
        try:
            result = self.execute(application_id, workload_id)
            if result['status'] in {'Submitted successfully', 'Already submitted', 'Blocked'}:
                self.registry.complete(workload_id)
            else:
                self.registry.wait_for_input(workload_id, '; '.join(result.get('blockers') or [result['status']]))
            return dict(result, workload_id=workload_id)
        except Exception as exc:
            self.registry.fail(workload_id, str(exc))
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='Consume Prepared with confirmed-result tracking')
    parser.add_argument('--application-id', help='Run exactly this application')
    parser.add_argument('--limit', type=int, default=1)
    parser.parse_args(argv)
    parser.error('Use the submit-prepared MCP stage task tools in ChatGPT native browser; this CLI cannot substitute for it')


if __name__ == '__main__':
    raise SystemExit(main())
