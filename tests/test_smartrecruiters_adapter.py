import json

import pytest

from adapters.job_boards.smartrecruiters import SmartRecruitersJobBoard


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode('utf-8')


def _detail():
    return {
        'id': '11',
        'name': 'Senior Backend Engineer',
        'releasedDate': '2026-10-15T09:43:25.761Z',
        'postingUrl': 'https://jobs.smartrecruiters.com/Example/11-backend',
        'applyUrl': 'https://jobs.smartrecruiters.com/Example/11-backend/apply',
        'location': {'country': 'il', 'fullLocation': 'Tel Aviv-Yafo, Israel'},
        'department': {'label': 'R&D'},
        'jobAd': {'sections': {
            'jobDescription': {'text': '<p>Java and Kafka services</p>'},
            'qualifications': {'text': '<p>Python</p>'},
        }},
    }


def test_keeps_israel_postings_and_uses_released_date():
    def opener(request):
        if 'offset=0' in request.full_url:
            return Response({
                'totalFound': 2,
                'content': [
                    {'id': '10', 'location': {'country': 'us', 'city': 'Austin',
                                              'fullLocation': 'Austin, United States'}},
                    {'id': '11', 'location': {'country': 'il', 'city': 'Tel Aviv-Yafo',
                                              'fullLocation': 'Tel Aviv-Yafo, Israel'}},
                ],
            })
        assert request.full_url.endswith('/postings/11')
        return Response(_detail())

    jobs = SmartRecruitersJobBoard({'Example': 'Example'}, opener=opener).list_jobs()
    assert len(jobs) == 1
    assert jobs[0]['company'] == 'Example'
    assert jobs[0]['countries'] == ['IL']
    assert jobs[0]['location'] == 'Tel Aviv-Yafo, Israel'
    assert 'Java and Kafka services' in jobs[0]['description']
    assert 'Python' in jobs[0]['description']
    assert jobs[0]['posted_at'] == '2026-10-15T09:43:25.761Z'
    assert jobs[0]['publication_date_kind'] == 'first_published'
    assert jobs[0]['source'] == 'smartrecruiters'
    assert jobs[0]['posting_url'].endswith('/11-backend')
    assert jobs[0]['departments'] == ['R&D']


def test_second_page_is_read_and_non_israel_jobs_skip_detail():
    calls = []

    def opener(request):
        calls.append(request.full_url)
        if 'offset=0' in request.full_url:
            return Response({'totalFound': 2, 'content': [
                {'id': '1', 'location': {'country': 'us', 'city': 'Austin'}}]})
        if 'offset=1' in request.full_url:
            return Response({'totalFound': 2, 'content': [
                {'id': '2', 'location': {'city': 'תל אביב'}}]})
        assert request.full_url.endswith('/postings/2')
        return Response(dict(_detail(), id='2', postingUrl='https://jobs.smartrecruiters.com/Example/2'))

    jobs = SmartRecruitersJobBoard({'Example': 'Example'}, opener=opener).list_jobs()
    assert len(jobs) == 1
    assert jobs[0]['source_id'] == '2'
    assert not any(url.endswith('/postings/1') for url in calls)


def test_malformed_response_is_not_an_empty_board():
    board = SmartRecruitersJobBoard({'example': 'Example'}, opener=lambda request: Response({'error': True}))
    with pytest.raises(RuntimeError, match='invalid response'):
        board.list_jobs()


def test_http_error_is_not_an_empty_board():
    import urllib.error

    def opener(request):
        raise urllib.error.HTTPError(request.full_url, 404, 'missing', hdrs=None, fp=None)

    with pytest.raises(RuntimeError, match='HTTP 404'):
        SmartRecruitersJobBoard({'missing': 'Missing'}, opener=opener).list_jobs()


def test_unreadable_descriptions_are_not_an_empty_board():
    import urllib.error

    def opener(request):
        if 'offset=0' in request.full_url:
            return Response({'totalFound': 1, 'content': [
                {'id': '5', 'location': {'country': 'il', 'city': 'Haifa'}}]})
        raise urllib.error.HTTPError(request.full_url, 503, 'down', hdrs=None, fp=None)

    with pytest.raises(RuntimeError, match='unreadable'):
        SmartRecruitersJobBoard({'example': 'Example'}, opener=opener).list_jobs()
