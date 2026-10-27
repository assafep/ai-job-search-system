import json

import pytest

from adapters.job_boards.comeet import ComeetJobBoard


class Response:
    def __init__(self, payload, html=False):
        self.payload = payload
        self.html = html

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        if self.html:
            return self.payload.encode('utf-8')
        return json.dumps(self.payload).encode('utf-8')


HTML = '<script>{"company_uid": "F2.004", "token": "ABC123DEF456ABC123DEF456ABC123DE"}</script>'


def opener(request):
    if 'careers-api' in request.full_url:
        assert 'token=ABC123DEF456ABC123DEF456ABC123DE' in request.full_url
        assert 'details=true' in request.full_url
        return Response([
            {
                'name': 'Senior Backend Engineer',
                'company_name': 'Claroty',
                'uid': '66.271',
                'department': 'Software Engineering',
                'employment_type': 'Full Time Employee',
                'experience_level': None,
                'is_internal': False,
                'location': {'name': 'Israel', 'country': 'IL', 'city': 'Tel Aviv'},
                'url_comeet_hosted_page': 'https://www.comeet.com/jobs/Claroty/F2.004/backend/66.271',
                'time_updated': '2026-10-18T18:23:51Z',
                'details': [
                    {'name': 'Description', 'value': '<p>Build Java and Scala services.</p>'},
                    {'name': 'Requirements', 'value': '<p>Kafka</p>'},
                ],
            },
            {
                'name': 'Backend Engineer',
                'is_internal': True,
                'experience_level': None,
                'location': {'country': 'IL', 'city': 'Haifa'},
                'url_comeet_hosted_page': 'https://www.comeet.com/jobs/internal',
                'details': [],
            },
            {
                'name': 'Backend Engineer',
                'is_internal': False,
                'experience_level': 'Junior',
                'location': {'country': 'IL', 'city': 'Haifa'},
                'url_comeet_hosted_page': 'https://www.comeet.com/jobs/junior',
                'details': [],
            },
            {
                'name': 'Old Backend Engineer',
                'is_internal': False,
                'experience_level': None,
                'location': {'country': 'IL', 'city': 'Haifa'},
                'url_comeet_hosted_page': 'https://www.comeet.com/jobs/old',
                'time_updated': '2024-01-01T00:00:00Z',
                'details': [{'name': 'Description', 'value': '<p>Java</p>'}],
            },
        ])
    assert request.full_url == 'https://www.comeet.com/jobs/Claroty/F2.004'
    return Response(HTML, html=True)


def test_comeet_reads_the_public_token_and_ignores_edit_time():
    jobs = ComeetJobBoard({'Claroty/F2.004': 'Claroty'}, opener=opener).list_jobs()
    assert len(jobs) == 1
    assert jobs[0]['company'] == 'Claroty'
    assert jobs[0]['role'] == 'Senior Backend Engineer'
    assert jobs[0]['countries'] == ['IL']
    assert jobs[0]['location'] == 'Tel Aviv, IL'
    assert jobs[0]['description'] == 'Build Java and Scala services.\nKafka'
    assert jobs[0]['posting_url'].endswith('/66.271')
    assert jobs[0]['source'] == 'comeet'
    assert jobs[0]['posted_at'] is None
    assert jobs[0]['updated_at'] == '2026-10-18T18:23:51Z'


def test_comeet_board_without_a_token_is_an_error():
    def empty(request):
        return Response('<html></html>', html=True)

    with pytest.raises(RuntimeError, match='careers token'):
        ComeetJobBoard({'F2.004': 'Claroty'}, opener=empty).list_jobs()


def test_comeet_submit_uses_native_submitter_by_default():
    from adapters.job_boards.interface import SubmissionContext
    board = ComeetJobBoard()
    calls = []
    context = SubmissionContext(
        application={'company': 'Example'}, posting_url='https://www.comeet.com/jobs/test/1.234/role/5.678',
        profile={'personal': {'name': 'Example'}}, package_bytes=b'%PDF-test',
        package_name='cv.pdf',
    )
    outcome = board.submit_application(context, lambda payload: calls.append(payload) or {'status': 'Prepared but not submitted', 'blockers': ['Test']})
    assert outcome['status'] == 'Prepared but not submitted'
    assert len(calls) == 1


def test_comeet_submit_local_playwright_dispatch(monkeypatch):
    from adapters.job_boards.interface import SubmissionContext
    from adapters.browser import comeet as browser
    received = []
    monkeypatch.setattr(browser, 'submit', lambda payload: received.append(payload) or {'status': 'Prepared but not submitted', 'blockers': ['Test']})
    context = SubmissionContext(
        application={'company': 'Example'}, posting_url='https://www.comeet.com/jobs/test/1.234/role/5.678',
        profile={'personal': {'name': 'Example'}}, package_bytes=b'%PDF-test',
        package_name='cv.pdf', host_context={'browser_backend': 'local-playwright'},
    )
    result = ComeetJobBoard().submit_application(context, None)
    assert result['status'] == 'Prepared but not submitted'
    assert len(received) == 1
    assert received[0]['package']['name'] == 'cv.pdf'
    assert received[0]['application']['posting_url'] == context.posting_url


def test_comeet_submit_local_playwright_rejects_non_comeet_url():
    from adapters.job_boards.interface import SubmissionContext
    context = SubmissionContext(
        application={}, posting_url='https://example.com/jobs/1',
        package_bytes=b'%PDF-test', package_name='cv.pdf',
        host_context={'browser_backend': 'local-playwright'},
    )
    with pytest.raises(ValueError, match='www.comeet.com'):
        ComeetJobBoard().submit_application(context, None)
