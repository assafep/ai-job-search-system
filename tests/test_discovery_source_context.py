from adapters.job_boards.interface import AdapterResult
from services.stage_executor import StageExecutor


def test_adzuna_source_is_not_fetched_without_explicit_source(monkeypatch):
    class Framework:
        def list_jobs(self, context, source):
            assert source == 'greenhouse'
            return AdapterResult(True, [])

        def search(self, *args):
            raise AssertionError('unselected search source must not run')

    monkeypatch.setattr('adapters.job_boards.framework.get_framework', Framework)
    context = {'source_names': ['greenhouse']}
    StageExecutor._enrich_discovery_context(context)
    assert context['job_postings'] == []


def test_adzuna_source_fetches_postings(monkeypatch):
    class Framework:
        def search(self, context, source):
            assert source == 'adzuna'
            assert context.query == 'python OR backend'
            assert context.locations == ['Tel Aviv']
            return AdapterResult(True, [{'company': 'Example', 'role': 'Engineer', 'posting_url': 'https://example'}])

    monkeypatch.setattr('adapters.job_boards.framework.get_framework', Framework)
    context = {'source_names': ['adzuna'], 'search_terms': ['python', 'backend'], 'locations': ['Tel Aviv']}
    StageExecutor._enrich_discovery_context(context)
    assert context['job_postings'][0]['company'] == 'Example'


def test_discovery_uses_onboarded_profile_countries(monkeypatch):
    class Framework:
        def search(self, context, source):
            assert source == 'freehire'
            assert context.countries == ['IL', 'US']
            assert context.query == ''
            return AdapterResult(True, [])

    monkeypatch.setattr('adapters.job_boards.framework.get_framework', Framework)
    context = {'source_names': ['freehire']}
    profile = {'job_search': {'countries': ['IL', 'US'], 'search_terms': []}}
    StageExecutor._enrich_discovery_context(context, profile)
    assert context['job_postings'] == []
