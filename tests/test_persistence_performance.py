"""Round-trip budgets and durability boundaries for storage optimizations."""

import copy
import json
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from application_service.google_docs_repository import GoogleDocsClient, GoogleDocsRepository
from application_service.google_drive_artifacts import GoogleDriveArtifactStore
from services.application_service_client import ApplicationServiceClient, ApplicationServiceError
from services.report_checkpoint import ReportCheckpoint
from tests.test_google_docs_repository import tab


def test_repeated_journal_reads_share_one_fetch_until_expiry(monkeypatch):
    clock = [0]
    monkeypatch.setattr('application_service.google_docs_repository.monotonic', lambda: clock[0])
    client = GoogleDocsClient('token', 'doc')
    client.cache_seconds = 5
    calls = []
    client.request = lambda *args: calls.append(args) or {'revisionId': str(len(calls))}
    for _ in range(50):
        assert client.get_document()['revisionId'] == '1'
    assert len(calls) == 1
    clock[0] = 5
    assert client.get_document()['revisionId'] == '2'
    client.batch_update([], '2')
    assert client.get_document()['revisionId'] == '4'


def test_mutation_refreshes_cached_journal_before_using_revision():
    states = [tab('state-' + name, name) for name in (
        'Bookmarked', 'Prepared', 'Manual Apply Required', 'Applied', 'Interviewing', 'Rejected', 'Offer')]
    states[0]['childTabs'] = [tab('app', 'Example / Engineer')]
    repository = GoogleDocsRepository('token', 'doc')
    requests = []
    def transport(method, url, payload=None):
        requests.append((method, payload))
        return copy.deepcopy({'revisionId': 'rev-' + str(len(requests)), 'tabs': states})
    repository.client.request = transport
    repository.list_applications()
    repository.list_events('app')
    assert len(requests) == 1
    repository.append_event('app', 'note', {'bullets': ['New note']}, 'note-1')
    assert [method for method, _ in requests] == ['GET', 'GET', 'POST']
    assert requests[-1][1]['writeControl']['requiredRevisionId'] == 'rev-2'
    repository.list_events('app')
    assert requests[-1][0] == 'GET'


def test_revision_conflict_does_not_replay_old_text_offsets():
    client = GoogleDocsClient('token', 'doc')
    calls = []
    def conflict(*args):
        calls.append(args)
        raise HTTPError('https://docs.test', 400, 'revision mismatch', {}, BytesIO(b'revision mismatch'))
    client.request = conflict
    with pytest.raises(RuntimeError, match='revision mismatch'):
        client.batch_update([{'insertText': {'location': {'index': 7}, 'text': 'note'}}], 'old')
    assert len(calls) == 1
    assert client._document is None


def test_checkpoints_reuse_folder_and_file_ids_without_caching_content(monkeypatch):
    clock = [0]
    monkeypatch.setattr('application_service.google_drive_artifacts.monotonic', lambda: clock[0])
    store = GoogleDriveArtifactStore('token', 'root')
    store.metadata_cache_seconds = 60
    calls = []
    def transport(method, url, payload=None, content_type='application/json'):
        calls.append(method)
        query = parse_qs(urlsplit(url).query)['q'][0]
        return {'files': [{'id': query, 'name': 'metadata'}]}
    store._request = transport
    store._replace_content = lambda *args: calls.append('PATCH') or {'id': 'report'}
    path = '.ai-job-search-system/workloads-internal-state/system-run/report.json'
    store.save_path(path, b'first')
    assert calls == ['GET', 'GET', 'GET', 'GET', 'PATCH']
    calls.clear()
    store.save_path(path, b'second')
    assert calls == ['PATCH']
    clock[0] = 60
    calls.clear()
    store.save_path(path, b'third')
    assert calls == ['GET', 'GET', 'GET', 'GET', 'PATCH']


def test_missing_receipt_is_never_cached():
    store = GoogleDriveArtifactStore('token', 'root')
    results = iter([{'files': []}, {'files': [{'id': 'new-receipt'}]}])
    store._request = lambda *args: next(results)
    assert store._find_child('receipts', 'app.json') is None
    assert store._find_child('receipts', 'app.json')['id'] == 'new-receipt'


def test_27_postings_need_four_report_uploads_and_preserve_final_results():
    uploads = []
    class Storage:
        def store_file(self, path, content, mime): uploads.append(json.loads(content))
    report = {'completed': []}
    checkpoint = ReportCheckpoint(Storage(), 'report.json', report, batch_size=10,
                                  interval_seconds=60, clock=lambda: 0)
    checkpoint.save(force=True)
    for number in range(27):
        report['completed'].append(number)
        checkpoint.save()
    checkpoint.save(force=True)
    assert len(uploads) == 4
    assert [len(item['completed']) for item in uploads] == [0, 10, 20, 27]


def test_checkpoint_time_limit_flushes_a_partial_batch():
    clock = [0]
    uploads = []
    class Storage:
        def store_file(self, *args): uploads.append(args)
    checkpoint = ReportCheckpoint(Storage(), 'report.json', {}, batch_size=10,
                                  interval_seconds=60, clock=lambda: clock[0])
    checkpoint.save(force=True)
    clock[0] = 60
    assert checkpoint.save()
    assert len(uploads) == 2


def test_client_timeout_is_bounded_and_counted(monkeypatch):
    def timeout(request, **kwargs):
        assert kwargs['timeout'] == 2
        raise TimeoutError('timed out')
    monkeypatch.setattr('urllib.request.urlopen', timeout)
    client = ApplicationServiceClient(timeout=2)
    with pytest.raises(ApplicationServiceError, match='timed out'):
        client.get_application('app')
    assert client.storage_statistics()['active_requests'] == 0
    assert client.storage_statistics()['operations']['GET /applications/{id}']['errors'] == 1
