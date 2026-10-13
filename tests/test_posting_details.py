import pytest
import json
from adapters.job_boards.posting_details import fetch_posting_details


def test_posting_details_decodes_source_description(monkeypatch):
    import json
    description = '<p>Build reliable event-driven services.</p>' * 30
    page = '<script type="application/ld+json">{}</script>'.format(json.dumps(
        {'@type': 'JobPosting', 'description': description}))

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, *args): return page.encode()

    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: Response())
    result = fetch_posting_details('https://example.test/job')
    assert 'Build reliable event-driven services.' in result['description']
    assert '<p>' not in result['description']
    assert result['source_url'] == 'https://example.test/job'


def test_unavailable_posting_blocks_package_preparation(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('unavailable')
    monkeypatch.setattr('urllib.request.urlopen', unavailable)
    with pytest.raises(ValueError, match='full job posting unavailable'):
        fetch_posting_details('https://example.test/job')


@pytest.mark.parametrize('url,expected_api,payload', [
    ('https://job-boards.greenhouse.io/example/jobs/123',
     'https://boards-api.greenhouse.io/v1/boards/example/jobs/123',
     {'content': '<p>Build reliable event-driven services.</p>' * 30}),
    ('https://jobs.smartrecruiters.com/Example/123-backend-engineer',
     'https://api.smartrecruiters.com/v1/companies/Example/postings/123',
     {'jobAd': {'sections': {'jobDescription': {'text': '<p>Build reliable event-driven services.</p>' * 30}}}}),
])
def test_exact_posting_api_avoids_loading_careers_page(monkeypatch, url, expected_api, payload):
    from io import BytesIO
    calls = []
    def opener(request, **kwargs):
        calls.append(request.full_url)
        return BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr('urllib.request.urlopen', opener)
    result = fetch_posting_details(url)
    assert calls == [expected_api]
    assert result['posting_url'] == url
    assert result['source_url'] == expected_api
    assert 'Build reliable event-driven services.' in result['description']


def test_fallbacks_share_a_time_budget(monkeypatch):
    import adapters.job_boards.posting_details as details
    clock = [0]
    calls = []
    monkeypatch.setattr(details, 'monotonic', lambda: clock[0])
    monkeypatch.setenv('POSTING_REQUEST_TIMEOUT_SECONDS', '12')
    monkeypatch.setenv('POSTING_FETCH_BUDGET_SECONDS', '25')
    def stalled(request, timeout):
        calls.append(timeout)
        clock[0] = 26
        raise TimeoutError()
    monkeypatch.setattr('urllib.request.urlopen', stalled)
    with pytest.raises(ValueError, match='retrieval time budget exhausted'):
        fetch_posting_details('https://www.linkedin.com/jobs/view/123')
    assert calls == [12]
