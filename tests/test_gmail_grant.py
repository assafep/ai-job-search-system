import json

from application_service.gmail_grant import (
    apply_refresh_token, notice_due, prepare_notice, run_maintenance, seed_from_environment,
)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_testing_grant_is_seeded_for_seven_days():
    record = seed_from_environment({}, {
        'GOOGLE_GMAIL_CLIENT_ID': 'id',
        'GOOGLE_GMAIL_CLIENT_SECRET': 'secret',
        'GOOGLE_GMAIL_REFRESH_TOKEN': 'refresh',
        'GOOGLE_GMAIL_NOTICE_EMAIL': 'ada@example.com',
    }, now=1_000)
    assert record['refresh_expires_at'] == 1_000 + 7 * 24 * 3600
    assert not notice_due(record, now=1_000)


def test_warning_is_sent_once_inside_two_days():
    sent = {'token': 0, 'mail': 0}

    def opener(request, timeout=30):
        if request.full_url.endswith('/token'):
            sent['token'] += 1
            return Response({'access_token': 'access', 'expires_in': 3600, 'refresh_token_expires_in': 86400})
        sent['mail'] += 1
        return Response({'id': 'message'})

    record = {
        'client_id': 'id', 'client_secret': 'secret', 'refresh_token': 'refresh',
        'notice_email': 'ada@example.com', 'refresh_expires_at': 50_000, 'scope': 'https://www.googleapis.com/auth/gmail.readonly',
    }
    updated, report = run_maintenance(record, 'https://ajss.example.com', opener=opener, now=1_000)
    assert report['status'] == 'notice_sent'
    assert report['connect_url'].startswith('https://ajss.example.com/gmail/connect?code=')
    assert '/gmail/renew?code=' in report['renew_url']
    again, second = run_maintenance(updated, 'https://ajss.example.com', opener=opener, now=1_000)
    assert second['status'] == 'current'
    assert sent['mail'] == 1
    replaced = apply_refresh_token(again, 'new-refresh', now=2_000, expires_in=604800)
    assert replaced['refresh_token'] == 'new-refresh'
    assert 'notice_code' not in replaced
    assert replaced['refresh_expires_at'] == 2_000 + 604800


def test_prepare_notice_names_both_links():
    record = {
        'refresh_token': 'refresh', 'refresh_expires_at': 10_000, 'notice_email': 'ada@example.com',
    }
    updated, message = prepare_notice(record, 'https://ajss.example.com', now=9_000)
    assert message['connect_url'] != message['renew_url']
    assert updated['notice_code']
