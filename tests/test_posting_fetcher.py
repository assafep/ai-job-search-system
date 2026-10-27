import json
from types import SimpleNamespace

import pytest

from services import posting_fetcher as service
from adapters.job_boards import transport
from adapters.job_boards.parsing import container_description

URL = 'https://example.com/jobs/123'
DESCRIPTION = 'Build reliable backend services and distributed infrastructure. ' * 20


def response(url, description=DESCRIPTION, status=200):
    body = '<script type="application/ld+json">' + json.dumps(
        {'@type': 'JobPosting', 'description': description}) + '</script>'
    return SimpleNamespace(status=status, body=body.encode(), url=url)


@pytest.fixture
def pool(tmp_path, monkeypatch):
    path = tmp_path / 'proxies.csv'
    path.write_text('ip,port,globally_routable\n8.8.8.8,80,True\n1.1.1.1,8080,True\n'
                    '127.0.0.1,80,True\n8.8.4.4,80,False\nbad,foo,True\n8.8.8.8,80,True\n')
    pool = transport.ProxyPool(path)
    monkeypatch.setattr(transport, '_pool', pool)
    monkeypatch.setenv('POSTING_PROXY_FILE', str(path))
    config = tmp_path / 'boards.json'
    config.write_text(json.dumps({'defaults': {
        'api': {'backend': 'scrapling'},
        'scraping': {'backend': 'scrapling', 'proxy_attempts': 4}}}))
    monkeypatch.setenv('JOB_BOARDS_CONFIG', str(config))
    return pool


def test_direct_read_keeps_bookmark_contract(monkeypatch, pool):
    from scrapling.fetchers import Fetcher
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        return response(url)
    monkeypatch.setattr(Fetcher, 'get', get)
    output, attempt = service.read_discovered_posting(URL, 'Example', 'Engineer')
    assert output == dict(company='Example', role='Engineer', posting_url=URL,
                          resolved_url=URL, description=DESCRIPTION.strip(), read_failure='')
    assert attempt is None
    assert len(calls) == 1 and calls[0]['proxy'] is None
    assert calls[0]['verify'] is True and calls[0]['retries'] == 1


def test_shell_then_dead_proxy_then_success(monkeypatch, pool):
    from scrapling.fetchers import Fetcher
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs['proxy'])
        if len(calls) == 1:
            return response(url, 'Please enable Javascript')
        if len(calls) == 2:
            raise TimeoutError('proxy failed')
        return response(url)
    monkeypatch.setattr(Fetcher, 'get', get)
    assert service.fetch_posting_details(URL)['description'] == DESCRIPTION.strip()
    assert calls[0] is None and len(set(calls[1:])) == 2
    assert len(pool.proxies) == 2
    assert pool.health[calls[1]][1] > transport.monotonic()
    assert pool.health[calls[2]][0] == 0


def test_bound_exhaustion_and_no_reuse(monkeypatch, pool):
    from scrapling.fetchers import Fetcher
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs['proxy'])
        return response(url, status=403)
    monkeypatch.setattr(Fetcher, 'get', get)
    with pytest.raises(ValueError, match='bounded routes'):
        service.fetch_posting_details(URL)
    assert len(calls) == 3 and len(set(calls)) == 3


def test_known_ats_and_redirect_source(monkeypatch, pool):
    from scrapling.fetchers import Fetcher
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status=200, body=json.dumps({'content': DESCRIPTION}).encode(), url=url + '?resolved=1')
    monkeypatch.setattr(Fetcher, 'get', get)
    result = service.fetch_posting_details('https://boards.greenhouse.io/example/jobs/123')
    assert calls == ['https://boards-api.greenhouse.io/v1/boards/example/jobs/123']
    assert result['source_url'].endswith('?resolved=1')


def test_generic_container_rejects_careers_shell():
    assert container_description('<main>' + DESCRIPTION + '</main>') == ''
    assert container_description('<div class="job-description">' + DESCRIPTION + '</div>') == DESCRIPTION.strip()


def test_missing_csv_is_direct_only(tmp_path):
    assert transport.ProxyPool(tmp_path / 'missing').choose(set()) is None


def test_time_budget_stops_routes(monkeypatch, pool):
    from scrapling.fetchers import Fetcher
    clock = [0]
    monkeypatch.setattr('adapters.job_boards.framework.monotonic', lambda: clock[0])
    monkeypatch.setattr('adapters.job_boards.posting_details.monotonic', lambda: clock[0])
    def get(url, **kwargs):
        clock[0] = 100
        raise TimeoutError()
    monkeypatch.setattr(Fetcher, 'get', get)
    with pytest.raises(ValueError, match='bounded routes'):
        service.fetch_posting_details(URL)
    assert not pool.health


def test_successful_proxy_reused_and_csv_reloaded(pool):
    first = pool.choose(set())
    pool.record(first, True)
    assert pool.choose(set()) == first
    pool.path.write_text('ip,port,globally_routable\n9.9.9.9,80,True\n')
    assert pool.choose(set()) == 'http://9.9.9.9:80'
    assert first not in pool.health


def test_https_downgrade_never_accepted(monkeypatch, pool):
    from scrapling.fetchers import Fetcher
    monkeypatch.setattr(Fetcher, 'get', lambda url, **kwargs: response('http://example.com/jobs/123'))
    with pytest.raises(ValueError, match='bounded routes'):
        service.fetch_posting_details(URL)


def test_no_proxy_use_for_plain_http(monkeypatch, pool):
    from scrapling.fetchers import Fetcher
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs['proxy'])
        return response(url, status=403)
    monkeypatch.setattr(Fetcher, 'get', get)
    with pytest.raises(ValueError):
        service.fetch_posting_details(URL.replace('https:', 'http:'))
    assert calls == [None]


@pytest.mark.parametrize('suffix', ['', '/', '?utm_source=test#description'])
def test_himalayas_uses_exact_api_before_html_or_proxies(monkeypatch, pool, suffix):
    from scrapling.fetchers import Fetcher
    posting = 'https://himalayas.app/companies/coinbase/jobs/staff-software-engineer'
    api = 'https://himalayas.app/jobs/api/companies/coinbase/jobs/staff-software-engineer'
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status=200, url=url, body=json.dumps({'job': {
            'guid': posting, 'companySlug': 'coinbase',
            'description': '<p>' + DESCRIPTION + '</p>',
        }}).encode())
    monkeypatch.setattr(Fetcher, 'get', get)
    output, attempt = service.read_discovered_posting(posting + suffix, 'Coinbase', 'Staff Software Engineer')
    assert len(calls) == 1
    assert calls[0][0] == api and calls[0][1]['proxy'] is None
    assert calls[0][1]['headers']['Accept'] == 'application/json'
    assert output['posting_url'] == posting + suffix
    assert output['resolved_url'] == api
    assert output['description'] == DESCRIPTION.strip()
    assert attempt is None
