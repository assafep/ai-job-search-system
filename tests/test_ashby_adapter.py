import json

from adapters.job_boards.ashby import AshbyJobBoard


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_only_explicitly_listed_jobs_with_secondary_country_and_publish_semantics():
    job = {'title': 'Senior Backend Engineer', 'isListed': True,
           'location': 'New York', 'secondaryLocations': [
               {'location': 'Tel Aviv', 'address': {'addressCountry': 'ISR'}}],
           'descriptionPlain': 'Build Scala services.',
           'jobUrl': 'https://jobs.ashbyhq.com/example/one',
           'publishedAt': '2026-10-15T00:00:00Z'}
    jobs = AshbyJobBoard({'example': 'Example'}, opener=lambda request: Response(
        {'jobs': [job, dict(job, isListed=False), dict(job, isListed=None)]})).list_jobs()
    assert len(jobs) == 1
    assert jobs[0]['countries'] == ['IL']
    assert jobs[0]['location'] == 'New York; Tel Aviv'
    assert jobs[0]['posted_at'] == job['publishedAt']
    assert jobs[0]['publication_date_kind'] == 'last_published'
    assert jobs[0]['description'] == 'Build Scala services.'


def test_malformed_response_is_not_treated_as_empty_board():
    import pytest
    board = AshbyJobBoard({'example': 'Example'}, opener=lambda request: Response({'error': 'unavailable'}))
    with pytest.raises(RuntimeError, match='invalid response'):
        board.list_jobs()
