import json

import pytest

from adapters.job_boards.lever import LeverJobBoard


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode('utf-8')


def test_lever_uses_creation_time_and_country_code():
    def opener(request):
        assert request.full_url.startswith('https://api.lever.co/v0/postings/cloudinary')
        return Response([{
            'text': 'Senior Backend Engineer',
            'categories': {'location': 'Israel', 'team': 'Engineering', 'allLocations': ['Israel']},
            'country': 'IL',
            'createdAt': 1790778163746,
            'descriptionPlain': 'Java services',
            'lists': [{'text': 'Requirements', 'content': '<li>Kafka</li>'}],
            'hostedUrl': 'https://jobs.lever.co/cloudinary/one',
            'id': 'one',
            'workplaceType': 'hybrid',
        }])

    jobs = LeverJobBoard({'cloudinary': 'Cloudinary'}, opener=opener).list_jobs()
    assert len(jobs) == 1
    assert jobs[0]['company'] == 'Cloudinary'
    assert jobs[0]['countries'] == ['IL']
    assert jobs[0]['location'] == 'Israel'
    assert 'Java services' in jobs[0]['description']
    assert 'Kafka' in jobs[0]['description']
    assert jobs[0]['posted_at']
    assert jobs[0]['publication_date_kind'] == 'first_published'
    assert jobs[0]['source'] == 'lever'
    assert jobs[0]['posting_url'] == 'https://jobs.lever.co/cloudinary/one'


def test_lever_falls_back_to_the_eu_host_after_a_missing_global_board():
    calls = []

    def opener(request):
        calls.append(request.full_url)
        if 'api.lever.co' in request.full_url:
            import urllib.error
            raise urllib.error.HTTPError(request.full_url, 404, 'missing', hdrs=None, fp=None)
        return Response([])

    assert LeverJobBoard({'example': 'Example'}, opener=opener).list_jobs() == []
    assert any('api.eu.lever.co' in url for url in calls)


def test_malformed_lever_response_is_not_an_empty_board():
    board = LeverJobBoard({'example': 'Example'}, opener=lambda request: Response({'error': True}))
    with pytest.raises(RuntimeError, match='invalid response'):
        board.list_jobs()
