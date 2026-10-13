import pytest
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
