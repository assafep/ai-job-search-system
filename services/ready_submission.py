"""Consume Ready for Auto-Apply, one item at a time, without regenerating packages."""

import argparse
import base64
import hashlib
import json
from io import BytesIO

from pypdf import PdfReader

from adapters.browser.submission import adapter_from_environment
from .event_dispatcher import dispatch
from .application_package import cover_matches_application
from .persistence_client import PersistenceApiClient, PersistenceApiError
from .runtime_config import load
from .workload_effort import effort_for_workflow
from .workload_registry import registry_from_environment


OUTCOMES = {'Submitted successfully', 'Already submitted', 'Prepared but not submitted',
            'Submission unconfirmed', 'Blocked'}


class ReadySubmission:
    def __init__(self, persistence, registry, adapter=None, manage_state=True):
        self.persistence = persistence
        self.registry = registry
        self.adapter = adapter or adapter_from_environment()
        self.manage_state = manage_state

    @staticmethod
    def receipt_path(application_id):
        from persistence_api.storage_layout import workload_state_path
        return workload_state_path('submit-ready', 'submission-receipts/{}.json'.format(application_id))

    def _receipt(self, application_id):
        try:
            result = self.persistence.read_file(self.receipt_path(application_id))
        except (KeyError, PersistenceApiError) as exc:
            if isinstance(exc, PersistenceApiError) and 'returned 404:' not in str(exc):
                raise
            return None
        if not result:
            return None
        data = result.get('content')
        if data is None:
            data = base64.b64decode(result['content_base64'])
        return json.loads(data)

    def _record_confirmed(self, application_id, receipt):
        confirmation = receipt.get('confirmation_text') or receipt.get('application_reference')
        if not confirmation:
            raise ValueError('a successful submission requires observed confirmation evidence')
        if not self.manage_state:
            return
        self.persistence.append_event(application_id, 'application_submitted', {
            'bullets': ['Application submitted successfully.', 'Confirmation: ' + str(confirmation)],
            'source': 'submission workflow',
        }, 'submission-confirmed-' + application_id)
        application = self.persistence.get_application(application_id)
        if application['status'] in {'Ready for Auto-Apply', 'Manual Apply Required'}:
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
        if application['status'] in {'Ready for Auto-Apply', target}:
            self.persistence.transition(application_id, target, {
                'source': 'auto-apply agent',
                'bullets': ['Auto-apply failed: ' + '; '.join(receipt.get('blockers') or [receipt.get('status', 'Unknown failure')]),
                            *(['Submission outcome is unconfirmed. Verify whether it was received before applying again.']
                              if receipt.get('status') == 'Submission unconfirmed' else [])],
            }, 'submission-failure-{}-{}'.format(target, application_id))

    def execute(self, application_id, workload_id):
        try:
            result = self._execute(application_id, workload_id)
            self._record_manual_required(application_id, result)
            return result
        except Exception as exc:
            app = self.persistence.get_application(application_id)
            if app is not None and app['status'] == 'Ready for Auto-Apply':
                receipt = self._receipt(application_id) or {'status': 'Blocked'}
                receipt = dict(receipt, application_id=application_id,
                               blockers=['Auto-apply error: ' + str(exc)])
                if receipt['status'] == 'Blocked':
                    receipt['failure_kind'] = 'system_error'
                self.persistence.store_file(self.receipt_path(application_id), json.dumps(receipt).encode(), 'application/json')
                self._record_manual_required(application_id, receipt)
                return receipt
            raise

    def _execute(self, application_id, workload_id):
        app = self.persistence.get_application(application_id)
        if app is None:
            raise KeyError(application_id)
        receipt = self._receipt(application_id)
        if receipt:
            if receipt['status'] in {'Submitted successfully', 'Already submitted'}:
                self._record_confirmed(application_id, receipt)
                return dict(receipt, replayed=True)
            if receipt['status'] == 'Submission unconfirmed':
                return dict(receipt, blockers=['Previous submission is unconfirmed; verify its outcome before attempting another submission.'], replayed=True)
            # Old runs may have left a pending approval receipt. Ready for
            # Auto-Apply now authorizes submission, so discard that stale
            # checkpoint and continue through package validation.
            if receipt.get('approval_pending'):
                receipt = None
            else:
                return dict(receipt, replayed=True)
        if app['status'] != 'Ready for Auto-Apply':
            raise ValueError('submission requires Ready for Auto-Apply; found ' + app['status'])
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
        context = {
            'application': app, 'applicant_profile': self.persistence.get_profile(),
            'package': artifact, 'package_bytes': content, 'package_sha256': digest,
            'reasoning_effort': effort_for_workflow('submit-ready'),
            'submission_authorized_by_queue': True,
        }
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
        self.persistence.store_file(self.receipt_path(application_id), json.dumps(receipt).encode(), 'application/json')
        if result['status'] in {'Submitted successfully', 'Already submitted'}:
            self._record_confirmed(application_id, receipt)
        return receipt

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
    parser = argparse.ArgumentParser(description='Consume Ready for Auto-Apply with confirmed-result tracking')
    parser.add_argument('--application-id', help='Run exactly this application')
    parser.add_argument('--limit', type=int, default=1)
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error('--limit must be positive')
    load(require=True)
    persistence = PersistenceApiClient()
    runner = ReadySubmission(persistence, registry_from_environment())
    ids = [args.application_id] if args.application_id else [a['application_id'] for a in
           persistence.list_applications(status='Ready for Auto-Apply')[:args.limit]]
    results = [runner.run_one(application_id) for application_id in ids]
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0 if all(r['status'] in {'Submitted successfully', 'Already submitted'} for r in results) else 2


if __name__ == '__main__':
    raise SystemExit(main())
