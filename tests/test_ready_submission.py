from services.ready_submission import ReadySubmission
from services.workload_registry import WorkloadRegistry
from persistence_api.repository import MemoryRepository
from services.document_generator import generate_application_pdf
from dataclasses import asdict
import pytest


class Client:
    def __init__(self):
        self.repository = MemoryRepository()
        app = self.repository.create_application('Example', 'Backend Engineer', 'https://example.test/job')
        self.id = app.application_id
        self.repository.transition(self.id, 'Ready for Auto-Apply')
        pdf = generate_application_pdf('Dear Example Hiring Team, Backend Engineer', '<p>Candidate resume</p>' * 60)
        self.repository.store_artifact(self.id, 'Candidate Cover Letter & Resume - Example - Backend Engineer.pdf', pdf, 'application/pdf')

    def get_application(self, id): return asdict(self.repository.get_application(id))
    def list_artifacts(self, id): return [asdict(a) for a in self.repository.list_artifacts(id)]
    def read_artifact(self, *args): return self.repository.read_artifact(*args)
    def read_file(self, *args): return self.repository.read_file(*args)
    def store_file(self, *args): return self.repository.store_file(*args)
    def append_event(self, *args): return self.repository.append_event(*args)
    def transition(self, *args): return self.repository.transition(*args)
    def get_profile(self): return {'personal': {'name': 'Candidate'}}


def make_runner(adapter):
    client = Client()
    runner = ReadySubmission(client, WorkloadRegistry(), adapter)
    return client, runner


def test_ready_queue_submits_without_approval():
    calls = []
    client, runner = make_runner(lambda ctx: calls.append(ctx) or {
        'status': 'Submitted successfully', 'confirmation_text': 'Application received'})
    result = runner.run_one(client.id)
    assert result['status'] == 'Submitted successfully'
    assert calls
    assert client.get_application(client.id)['status'] == 'Applied'


def test_confirmed_submission_promotes_and_retry_does_not_submit_twice():
    calls = []
    def submit(ctx):
        calls.append(ctx)
        return {'status': 'Submitted successfully', 'confirmation_text': 'Application received'}
    client, runner = make_runner(submit)
    result = runner.run_one(client.id)
    assert result['status'] == 'Submitted successfully'
    assert client.get_application(client.id)['status'] == 'Applied'
    runner.run_one(client.id)
    assert len(calls) == 1


def test_unconfirmed_submission_moves_to_manual_and_prevents_duplicate_attempt():
    calls = []
    def submit(ctx):
        calls.append(ctx)
        return {'status': 'Submission unconfirmed', 'blockers': ['No confirmation page']}
    client, runner = make_runner(submit)
    runner.run_one(client.id)
    runner.run_one(client.id)
    assert len(calls) == 1
    assert client.get_application(client.id)['status'] == 'Manual Apply Required'


def test_interrupted_adapter_cannot_be_retried_as_a_new_submission():
    calls = []
    def interrupted(ctx):
        calls.append(ctx)
        raise RuntimeError('connection lost after submit')
    client, runner = make_runner(interrupted)
    first = runner.run_one(client.id)
    assert 'connection lost' in first['blockers'][0]
    result = runner.run_one(client.id)
    assert result['status'] == 'Submission unconfirmed'
    assert len(calls) == 1
    assert client.get_application(client.id)['status'] == 'Manual Apply Required'


def test_success_without_receipt_cannot_promote_application():
    client, runner = make_runner(lambda ctx: {'status': 'Submitted successfully'})
    result = runner.run_one(client.id)
    assert 'no confirmation' in result['blockers'][0]
    assert client.get_application(client.id)['status'] == 'Manual Apply Required'


def test_unsupported_site_failure_records_reason_and_moves_to_manual():
    client, runner = make_runner(lambda ctx: {'status': 'Blocked', 'blockers': ['Unsupported recruiting site']})
    runner.run_one(client.id)
    assert client.get_application(client.id)['status'] == 'Manual Apply Required'
    events = client.repository.list_events(client.id)
    assert any('Unsupported recruiting site' in str(e.data.get('bullets', [])) for e in events)


def test_preflight_failure_is_explained_and_routed_to_manual():
    client, runner = make_runner(lambda ctx: pytest.fail('invalid package must not reach browser'))
    client.list_artifacts = lambda id: []
    result = runner.run_one(client.id)
    assert result['status'] == 'Blocked'
    assert 'combined cover-letter/resume PDF' in result['blockers'][0]
    assert client.get_application(client.id)['status'] == 'Manual Apply Required'


def test_human_verification_rejection_prevents_automatic_resubmission():
    calls = []
    def rejected(context):
        calls.append(context)
        return {'status': 'Blocked', 'requires_human_verification': True,
                'submission_rejected': True, 'blockers': ['Human verification required']}
    client, runner = make_runner(rejected)
    runner.run_one(client.id)
    result = runner.run_one(client.id)
    assert result['replayed'] is True
    assert len(calls) == 1
    assert client.get_application(client.id)['status'] == 'Manual Apply Required'


def test_existing_human_verification_receipt_is_migrated_without_browser_retry():
    import json
    calls = []
    client, runner = make_runner(lambda ctx: calls.append(ctx))
    client.store_file(runner.receipt_path(client.id), json.dumps({
        'status': 'Blocked', 'requires_human_verification': True,
        'blockers': ['Human check rejected submission'],
    }).encode(), 'application/json')
    runner.run_one(client.id)
    assert client.get_application(client.id)['status'] == 'Manual Apply Required'
    assert calls == []
