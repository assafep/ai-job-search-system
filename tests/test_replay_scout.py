from dataclasses import asdict, is_dataclass
from types import SimpleNamespace
from threading import Barrier, Lock

import pytest

from persistence_api.repository import MemoryRepository
from services.workflow_runtime import WorkflowRuntime
from services.workload_registry import WorkloadRegistry
from services.replay_scout import run
from adapters.job_boards.url_posting import fetch_exact_posting


def test_exact_url_http_failure_has_no_alternative_request(monkeypatch):
    calls = []
    def invoke(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='Forbidden\n403\nhttps://example.test/job', stderr='')
    monkeypatch.setattr('adapters.job_boards.url_posting.shutil.which', lambda name: '/usr/bin/curl')
    monkeypatch.setattr('adapters.job_boards.url_posting.subprocess.run', invoke)
    with pytest.raises(ValueError, match='HTTP 403'):
        fetch_exact_posting('https://example.test/job', 'Backend')
    assert len(calls) == 1
    assert calls[0][-1] == 'https://example.test/job'


def test_replay_runs_scout_and_bookmarks_without_preparation(monkeypatch, candidate_profile):
    repository = MemoryRepository()
    repository.save_profile(candidate_profile)
    application = repository.create_application('Example', 'Backend', 'https://example.test/job')
    description = 'Original backend role requirements. ' * 30

    class Client:
        def _request(self, method, path, payload=None):
            if method == 'GET':
                return [{'tab_id': 'replay', 'title': 'Replay', 'parent_tab_id': None},
                        {'tab_id': application.application_id, 'title': 'Example / Backend',
                         'parent_tab_id': 'replay',
                         'paragraphs': [{'text': 'Posting URL: ' + application.posting_url}]}]
            return asdict(application)
        def __getattr__(self, name):
            def invoke(*args, **kwargs):
                result = getattr(repository, name)(*args, **kwargs)
                return asdict(result) if is_dataclass(result) else result
            return invoke

    def provider(prompt, context):
        assert context['read_posting_url'] == application.posting_url
        assert 'description' not in context['job_postings'][0]
        return {'candidates': [{'company': 'Example', 'role': 'Backend',
                                'posting_url': application.posting_url, 'description': description}]}
    client = Client()
    outcomes = run(client, WorkflowRuntime(WorkloadRegistry(), client), provider)
    assert outcomes[0]['status'] == 'bookmarked'
    assert application.status == 'Bookmarked'
    artifacts = repository.list_artifacts(application.application_id)
    assert len(artifacts) == 1 and artifacts[0].mime_type == 'application/json'


def test_replay_exception_moves_to_dead_letters_and_can_fail_again_after_restore():
    repository = MemoryRepository()
    application = repository.create_application('Example', 'Backend', 'https://example.test/broken')
    application.status = 'Replay'
    class Client:
        def _request(self, method, path, payload=None):
            return repository.journal_tabs()
        def __getattr__(self, name):
            def invoke(*args, **kwargs):
                value = getattr(repository, name)(*args, **kwargs)
                return asdict(value) if is_dataclass(value) else value
            return invoke
    def broken(prompt, context):
        raise RuntimeError('missing scout configuration')
    client = Client()
    runtime = WorkflowRuntime(WorkloadRegistry(), client)
    for attempt in range(2):
        if attempt:
            repository.transition(application.application_id, 'Replay')
        outcomes = run(client, runtime, broken)
        assert outcomes[0]['routed_to'] == 'Dead Letters Queue'
        assert application.status == 'Dead Letters Queue'
    events = [e for e in repository.list_events(application.application_id)
              if 'missing scout configuration' in str(e.data.get('bullets', []))]
    assert len(events) == 2


def test_seven_replay_workers_claim_each_posting_once_and_report_all_workers(capsys):
    repository = MemoryRepository()
    for index in range(7):
        app = repository.create_application('Company {}'.format(index), 'Backend',
                                            'https://example.test/{}'.format(index))
        app.status = 'Replay'
    barrier = Barrier(7)
    lock = Lock()
    urls = []
    class Client:
        def _request(self, method, path):
            return repository.journal_tabs()
        def get_profile(self):
            return {}
    def provider(prompt, context):
        with lock:
            urls.append(context['read_posting_url'])
        barrier.wait(timeout=5)
        return {'candidates': []}
    client = Client()
    outcomes = run(client, WorkflowRuntime(WorkloadRegistry(), client), provider,
                   workers=7, progress_seconds=0.01)
    assert len(urls) == len(set(urls)) == len(outcomes) == 7
    assert {item['worker'] for item in outcomes} == set(range(1, 8))
    assert all(item['status'] == 'declined' for item in outcomes)
    output = capsys.readouterr().out
    assert all('Worker {}:'.format(worker) in output for worker in range(1, 8))
