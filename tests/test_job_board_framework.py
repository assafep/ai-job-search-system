import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest

from adapters.job_boards.configuration import BoardConfiguration, FetchPolicy
from adapters.job_boards.framework import AdapterFramework, builtin_adapters
from adapters.job_boards.interface import (
    BookmarkContext, DiscoveryContext, JobBoardAdapter, SubmissionContext,
)
from adapters.job_boards.transport import ReadSession

DESCRIPTION = 'Build reliable backend services with Python and distributed infrastructure. ' * 20
URL = 'https://boards.greenhouse.io/example/jobs/123'


def html_job(url=URL, company='Example'):
    return '<script type="application/ld+json">' + json.dumps({
        '@type': 'JobPosting', 'url': url, 'title': 'Backend Engineer',
        'description': DESCRIPTION, 'hiringOrganization': {'name': company},
        'jobLocation': {'address': {'addressLocality': 'London', 'addressCountry': 'GB'}},
    }) + '</script>'


def framework(reader, **kwargs):
    return AdapterFramework(source_reader=reader, config={'defaults': {}}, **kwargs)


def test_builtins_implement_contract_and_resolve_provider_hosts():
    runtime = framework(lambda *args: pytest.fail('registration must not fetch'))
    assert all(isinstance(board, JobBoardAdapter) for board in builtin_adapters())
    for url, provider in [
        (URL, 'greenhouse'), ('https://jobs.ashbyhq.com/acme/id', 'ashby'),
        ('https://jobs.eu.lever.co/acme/id', 'lever'),
        ('https://tenant.careers.hibob.com/jobs/id', 'hibob'),
        ('https://jobs.akamai.com/en/sites/CX_1/job/123', 'oracle'),
        ('https://www.nimbleway.com/careers?uid=123', 'nimble'),
        ('https://boards.greenhouse.io.evil.test/example/jobs/123', 'default'),
    ]:
        assert runtime.resolve_provider(url) == provider


def test_api_success_never_scrapes_and_keeps_original_url():
    calls = []
    def reader(url, headers, timeout):
        calls.append(url)
        return json.dumps({'id': 123, 'content': DESCRIPTION})
    result = framework(reader).fetch_details(BookmarkContext(URL))
    assert result.success and result.metadata['mode'] == 'api'
    assert result.data['posting_url'] == URL
    assert calls == ['https://boards-api.greenhouse.io/v1/boards/example/jobs/123']


@pytest.mark.parametrize('failure', [OSError('HTTP 429'), OSError('HTTP 403'), ValueError('invalid JSON')])
def test_detail_api_failure_falls_back_to_board_html(failure):
    calls = []
    def reader(url, headers, timeout):
        calls.append(url)
        if 'boards-api' in url:
            raise failure
        return html_job()
    result = framework(reader).fetch_details(BookmarkContext(URL))
    assert result.success and result.metadata['mode'] == 'scraping'
    assert result.data['source_url'] == URL
    assert len(result.metadata['attempts']) == 1 and len(calls) == 2


def test_missing_api_credentials_allow_public_search_fallback_without_api_request(monkeypatch):
    monkeypatch.delenv('ADZUNA_APP_ID', raising=False)
    monkeypatch.delenv('ADZUNA_APP_KEY', raising=False)
    calls = []
    def reader(url, headers, timeout):
        calls.append(url)
        return html_job('https://www.adzuna.co.uk/jobs/details/123')
    result = framework(reader).search(DiscoveryContext(query='backend', countries=['GB']), 'adzuna')
    assert result.success and result.metadata['mode'] == 'scraping'
    assert len(calls) == 1 and calls[0].startswith('https://www.adzuna.co.uk/search?')
    assert 'credentials' in result.metadata['attempts'][0]['error']


def test_empty_api_result_is_success_not_a_reason_to_scrape():
    calls = []
    def reader(url, headers, timeout):
        calls.append(url)
        return '{"data": []}'
    result = framework(reader).search(DiscoveryContext(), 'freehire')
    assert result.success and result.data == [] and len(calls) == 1


def test_employer_list_falls_back_to_its_public_board():
    calls = []
    def reader(url, headers, timeout):
        calls.append(url)
        if 'boards-api' in url:
            raise OSError('HTTP 429')
        return html_job()
    result = framework(reader).list_jobs(DiscoveryContext(boards={'example': 'Example'}), 'greenhouse')
    assert result.success and result.data[0]['posting_url'] == URL
    assert calls[-1] == 'https://boards.greenhouse.io/example'


def test_shells_and_mismatched_records_are_not_successful_details():
    def reader(url, headers, timeout):
        if 'boards-api' in url:
            return json.dumps({'id': 999, 'content': DESCRIPTION})
        return html_job(URL.replace('123', '999'))
    result = framework(reader).fetch_details(BookmarkContext(URL))
    assert not result.success
    assert 'different posting' in result.error and 'no full job description' in result.error


def test_configuration_merges_provider_options_and_resolves_relative_proxy_path(tmp_path):
    config = BoardConfiguration(tmp_path / 'boards.json', {
        'defaults': {'budget_seconds': 40, 'scraping': {
            'backend': 'scrapling', 'proxy_attempts': 2, 'proxy_file': 'routes.csv'}},
        'providers': {'greenhouse': {'scraping': {'timeout_seconds': 9}}},
    })
    budget, policies = config.for_provider('greenhouse')
    assert budget == 40 and policies['scraping'].timeout_seconds == 9
    assert policies['scraping'].proxy_attempts == 2
    assert policies['scraping'].proxy_file == str(tmp_path / 'routes.csv')
    assert config.for_provider('lever')[1]['scraping'].timeout_seconds == 6


@pytest.mark.parametrize('section', [
    {'scraping': {'typo': 1}}, {'scraping': {'timeout_seconds': 0}},
    {'scraping': {'proxy_attempts': 21}}, {'scraping': {'direct': False}},
    {'scraping': {'backend': 'browser'}}, {'budget_seconds': float('nan')},
])
def test_invalid_strategy_fails_before_network(section):
    with pytest.raises(ValueError):
        BoardConfiguration(data={'defaults': section})


def test_provider_policy_applies_to_api_and_scrape_requests():
    calls = []
    runtime = AdapterFramework(config={
        'defaults': {'api': {'timeout_seconds': 1}, 'scraping': {'timeout_seconds': 2}},
        'providers': {'greenhouse': {'api': {'timeout_seconds': 3}, 'scraping': {'timeout_seconds': 4}}},
    }, source_reader=lambda url, headers, timeout: calls.append((url, timeout)) or (
        '{}' if 'boards-api' in url else html_job()))
    assert runtime.fetch_details(BookmarkContext(URL)).success
    assert [timeout for _, timeout in calls] == [3, 4]


def test_api_and_scraping_share_one_deadline():
    now, calls = [0], []
    def reader(url, headers, timeout):
        calls.append(timeout)
        now[0] += 4
        raise OSError('HTTP 429')
    runtime = AdapterFramework(config={'defaults': {'budget_seconds': 5}},
                               source_reader=reader, clock=lambda: now[0])
    assert not runtime.fetch_details(BookmarkContext(URL)).success
    assert calls == [5, 1]


def test_unknown_provider_is_a_configuration_error():
    with pytest.raises(ValueError, match='Unknown job board'):
        framework(lambda *args: pytest.fail('must not fetch')).search(DiscoveryContext(), 'typo')


def test_shared_framework_does_not_mix_concurrent_employer_contexts():
    def reader(url, headers, timeout):
        token = url.split('/boards/')[1].split('/')[0]
        return json.dumps({'jobs': [{'id': token, 'title': 'Backend',
                                    'absolute_url': 'https://example.test/' + token}]})
    runtime = framework(reader)
    def run(token):
        return runtime.list_jobs(DiscoveryContext(boards={token: token}), 'greenhouse').unwrap()[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(run, ['one', 'two']))
    assert first['company'] == 'one' and second['company'] == 'two'


def test_submit_dispatches_once_and_does_not_retry_uncertainty():
    calls = []
    runtime = framework(lambda *args: pytest.fail('submission must not use read transport'),
                        submitter=lambda context: calls.append(context) or {
                            'status': 'Submission unconfirmed', 'blockers': ['No confirmation observed']})
    context = SubmissionContext(application={}, posting_url=URL, host_context={'prepared': True})
    result = runtime.submit(context)
    assert result.data['status'] == 'Submission unconfirmed' and calls == [{'prepared': True}]
    assert result.metadata['provider'] == 'greenhouse'


@pytest.mark.parametrize('outcome', [
    None,
    {'status': 'Unknown'},
    {'status': 'Submitted successfully', 'confirmation_text': '  '},
    {'status': 'Submitted successfully', 'confirmation_text': 'Received', 'requires_human_verification': True},
    {'status': 'Blocked', 'blockers': ['  ']},
])
def test_submit_rejects_invalid_outcomes_after_one_dispatch(outcome):
    calls = []
    runtime = framework(lambda *args: pytest.fail('submission must not use read transport'),
                        submitter=lambda context: calls.append(context) or outcome)
    with pytest.raises(ValueError):
        runtime.submit(SubmissionContext(application={}, posting_url=URL))
    assert len(calls) == 1


def test_submit_exceptions_propagate_without_fallback():
    calls = []
    def submit(context):
        calls.append(context)
        raise OSError('connection lost after send')
    with pytest.raises(OSError):
        framework(None, submitter=submit).submit(SubmissionContext({}, URL))
    assert len(calls) == 1


def test_no_native_host_means_explicit_blocked_submission():
    result = framework(None).submit(SubmissionContext({}, URL))
    assert not result.success and result.data['status'] == 'Blocked'
    assert 'native' in result.error


def test_transport_size_limit_and_redirect_downgrade():
    with pytest.raises(ValueError, match='size limit'):
        ReadSession(FetchPolicy(max_response_bytes=8), source_reader=lambda *args: 'x' * 9).read_text(URL)
    class Response(BytesIO):
        def geturl(self):
            return 'http://example.test/job'
    with pytest.raises(ValueError, match='away from HTTPS'):
        ReadSession(FetchPolicy(), opener=lambda *args, **kwargs: Response(b'body')).read_text(URL)


@pytest.mark.parametrize('provider,url,mode,body', [
    ('greenhouse', URL, 'api', json.dumps({'id': 123, 'content': DESCRIPTION})),
    ('comeet', 'https://www.comeet.com/jobs/example/12.345/backend/67.890', 'scraping',
     '<script>POSITION_DATA = ' + json.dumps({'custom_fields': {'details': [
         {'name': 'Description', 'value': DESCRIPTION}]}}) + ';</script>'),
])
def test_board_specific_capabilities_obey_external_proxy_and_backend_policy(monkeypatch, provider, url, mode, body):
    from scrapling.fetchers import Fetcher
    from types import SimpleNamespace
    calls, recorded = [], []
    class Pool:
        def choose(self, excluded):
            return 'http://8.8.8.8:80'
        def record(self, proxy, success):
            recorded.append(success)
    def get(target, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status=200, body=body.encode(), url=target)
    monkeypatch.setattr(Fetcher, 'get', get)
    runtime = AdapterFramework(config={
        'defaults': {}, 'providers': {provider: {mode: {
            'backend': 'scrapling', 'direct': False, 'proxy_attempts': 1,
            'timeout_seconds': 2, 'impersonate': 'firefox', 'max_redirects': 2}}},
    }, proxy_pool=Pool())
    result = runtime.fetch_details(BookmarkContext(url))
    assert result.success and result.metadata['mode'] == mode
    assert len(calls) == 1 and calls[0]['proxy'] == 'http://8.8.8.8:80'
    assert calls[0]['timeout'] == 2 and calls[0]['impersonate'] == 'firefox'
    assert calls[0]['max_redirects'] == 2 and recorded == [True]


@pytest.mark.parametrize('provider,url,payload', [
    ('ashby', 'https://jobs.ashbyhq.com/acme/123', {'jobs': [
        {'id': 'wrong', 'isListed': True, 'descriptionPlain': 'Wrong. ' * 100},
        {'id': '123', 'isListed': True, 'descriptionPlain': DESCRIPTION}]}),
    ('lever', 'https://jobs.eu.lever.co/acme/123', [
        {'id': 'wrong', 'descriptionPlain': 'Wrong. ' * 100},
        {'id': '123', 'descriptionPlain': DESCRIPTION}]),
])
def test_exact_job_is_selected_from_employer_api_feed(provider, url, payload):
    result = framework(lambda *args: json.dumps(payload)).fetch_details(BookmarkContext(url))
    assert result.success and result.metadata['provider'] == provider
    assert result.metadata['mode'] == 'api'
    assert result.data['description'] == DESCRIPTION


def test_html_search_preserves_country_and_query_scope_after_api_failure(monkeypatch):
    monkeypatch.delenv('ADZUNA_APP_ID', raising=False)
    monkeypatch.delenv('ADZUNA_APP_KEY', raising=False)
    result = framework(lambda *args: html_job()).search(
        DiscoveryContext(query='accountant', countries=['GB']), 'adzuna')
    assert result.success and result.data == []
    def reader(url, headers, timeout):
        if '/api/' in url:
            raise OSError('HTTP 429')
        return html_job()
    result = framework(reader).search(DiscoveryContext(query='backend', countries=['IL']), 'freehire')
    assert result.success and result.data == []
