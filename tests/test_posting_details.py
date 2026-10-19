import pytest
import json
from adapters.job_boards.posting_details import fetch_posting_details


def test_posting_filenames_keep_different_urls_separate():
    from adapters.job_boards.posting_details import posting_artifact_name
    first = posting_artifact_name('HiBob', 'Backend', 'https://one.careers.hibob.com/jobs/id')
    second = posting_artifact_name('HiBob', 'Backend', 'https://two.careers.hibob.com/jobs/id')
    assert first != second
    assert first == posting_artifact_name('HiBob', 'Backend', 'https://one.careers.hibob.com/jobs/id?utm_source=mail')
    assert first.startswith('Job Posting - ') and first.endswith('.json')


def test_comeet_null_position_is_an_ordinary_unreadable_posting():
    from adapters.job_boards.posting_details import _extract_comeet
    assert _extract_comeet('<script>POSITION_DATA = null;</script>') == ''


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


def test_hibob_public_job_ad_endpoint_uses_tenant_header_and_exact_id(monkeypatch):
    import json
    payload = {'jobAdDetails': [{
        'id': '3ad5440b-6e7a-4a06-a4ec-3555a90716d1',
        'title': 'Senior Backend Engineer',
        'description': '<p>' + ('Build reliable services. ' * 30) + '</p>',
        'requirements': '<ul><li>Backend experience</li></ul>',
        'responsibilities': '<ul><li>Ship software</li></ul>',
        'benefits': '<p>Equity and benefits</p>',
        'site': 'IL', 'country': 'Israel', 'workspaceType': 'Hybrid',
    }]}
    seen = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, *args): return json.dumps(payload).encode()

    def opener(request, **kwargs):
        seen['url'] = request.full_url
        seen['header'] = request.headers.get('Companyidentifier')
        return Response()

    monkeypatch.setattr('urllib.request.urlopen', opener)
    result = fetch_posting_details(
        'https://hibob-fa0ad69d0cb34a.careers.hibob.com/jobs/'
        '3ad5440b-6e7a-4a06-a4ec-3555a90716d1')
    assert seen['url'].endswith('/api/job-ad')
    assert seen['header'] == 'hibob-fa0ad69d0cb34a'
    assert 'Build reliable services.' in result['description']
    assert result['source_url'].endswith('/api/job-ad')


def test_comeet_serialized_position_data_is_extracted(monkeypatch):
    import json
    payload = {
        'custom_fields': {'details': [
            {'name': 'Description', 'value': '<p>' + ('Build DLP systems. ' * 30) + '</p>'},
            {'name': 'Requirements', 'value': '<ul><li>Backend experience</li></ul>'},
        ]},
        'location': {'name': 'Tel Aviv Office', 'city': 'Tel Aviv', 'country': 'IL'},
    }
    source = '<script>POSITION_DATA = ' + json.dumps(payload) + ';</script>'

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, *args): return source.encode()

    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: Response())
    result = fetch_posting_details(
        'https://www.comeet.com/jobs/example/87.00B/backend/3F.273')
    assert 'Build DLP systems.' in result['description']
    assert 'Requirements' in result['description']


def test_akamai_branded_url_uses_exact_oracle_requisition(monkeypatch):
    payload = {'items': [{
        'Id': 3958,
        'Title': 'Senior Backend Infrastructure Engineer',
        'ExternalDescriptionStr': '<p>' + ('Build secure systems. ' * 30) + '</p>',
        'ExternalResponsibilitiesStr': '<p>Operate the platform.</p>',
        'ExternalQualificationsStr': '<p>Backend experience.</p>',
        'PrimaryLocation': 'Israel',
        'WorkplaceType': 'Remote',
    }]}
    calls = []

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, *args): return json.dumps(payload).encode()

    def opener(request, **kwargs):
        calls.append(request.full_url)
        return Response()

    monkeypatch.setattr('urllib.request.urlopen', opener)
    url = 'https://jobs.akamai.com/en/sites/CX_1/job/3958/?utm_source=jobshare'
    result = fetch_posting_details(url)
    assert calls == [
        'https://fa-extu-saasfaprod1.fa.ocs.oraclecloud.com/'
        'hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?'
        'onlyData=true&finder=ById;Id=3958&expand=all'
    ]
    assert result['posting_url'] == url
    assert result['source_url'] == calls[0]
    assert 'Build secure systems.' in result['description']
