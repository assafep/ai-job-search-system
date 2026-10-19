import json

import pytest

from adapters.job_boards.himalayas import HimalayasJobBoard


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode('utf-8')


def test_himalayas_search_keeps_israel_restrictions_and_marks_worldwide_empty():
    def opener(request):
        assert 'country=IL' in request.full_url
        assert 'exclude_worldwide=true' in request.full_url
        assert 'sort=recent' in request.full_url
        return Response({'jobs': [
            {
                'title': 'Senior Backend Engineer',
                'companyName': 'Example',
                'applicationLink': 'https://himalayas.app/companies/example/jobs/backend',
                'description': '<p>Build Java services in Scala.</p>',
                'locationRestrictions': ['Israel'],
                'pubDate': 1792235761,
                'guid': 'job-1',
                'employmentType': 'Full Time',
                'seniority': ['Senior'],
            },
            {
                'title': 'Backend Engineer',
                'companyName': 'Worldwide Co',
                'applicationLink': 'https://himalayas.app/companies/world/jobs/backend',
                'description': '<p>Python</p>',
                'locationRestrictions': [],
                'pubDate': 1792235761,
                'guid': 'job-2',
            },
            {
                'title': 'Backend Engineer',
                'companyName': 'Everywhere',
                'applicationLink': 'https://himalayas.app/companies/everywhere/jobs/backend',
                'description': '<p>Java</p>',
                'locationRestrictions': ['Israel', 'USA', 'UK', 'Germany', 'France', 'Spain', 'Brazil'],
                'pubDate': 1792235761,
                'guid': 'job-3',
            },
        ]})

    jobs = HimalayasJobBoard(opener=opener).search('backend', country='IL', max_pages=1)
    by_url = {job['posting_url']: job for job in jobs}
    israel = by_url['https://himalayas.app/companies/example/jobs/backend']
    assert israel['countries'] == ['IL']
    assert israel['location'] == 'Israel'
    assert israel['description'] == 'Build Java services in Scala.'
    assert israel['posted_at'].startswith('2026-')
    assert israel['source'] == 'himalayas'
    assert by_url['https://himalayas.app/companies/world/jobs/backend']['countries'] == []
    assert 'https://himalayas.app/companies/everywhere/jobs/backend' not in by_url


def test_himalayas_rejects_a_malformed_page():
    board = HimalayasJobBoard(opener=lambda request: Response({'error': True}))
    with pytest.raises(RuntimeError, match='invalid response'):
        board.search('backend', max_pages=1)
