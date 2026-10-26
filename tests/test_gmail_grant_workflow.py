from services.gmail_grant_workflow import execute


def test_workflow_sends_the_notice_and_does_not_keep_the_access_token():
    calls = []

    class Client:
        def prepare_gmail_grant(self):
            return {
                'status': 'due',
                'access_token': 'secret-access',
                'refresh_expires_at': 10,
                'message': {
                    'to': 'ada@example.com', 'subject': 'expires', 'body': 'renew',
                    'connect_url': 'https://ajss.example.com/gmail/connect?code=1',
                    'renew_url': 'https://ajss.example.com/gmail/renew?code=1',
                },
            }

        def bulk_update_items(self, items):
            assert len(items) == 1 and items[0]['operation'] == 'acknowledge_gmail_notice'
            assert items[0]['data'] == {'refresh_expires_at': 10}
            calls.append('ack')
            return {'failed': 0, 'results': [{'status': 'succeeded', 'result': {'status': 'notice_sent', 'refresh_expires_at': 10}}]}

    def opener(request, timeout=30):
        calls.append(request.full_url)
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"id":"m"}'
        return Response()

    result = execute(Client(), opener=opener)
    assert result['status'] == 'notice_sent'
    assert 'secret-access' not in str(result)
    assert calls == ['https://gmail.googleapis.com/gmail/v1/users/me/messages/send', 'ack']


def test_workflow_does_not_send_when_the_grant_is_current():
    class Client:
        def prepare_gmail_grant(self):
            return {'status': 'current'}
        def acknowledge_gmail_notice(self):
            raise AssertionError('notice was not due')

    assert execute(Client(), opener=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('sent')))['status'] == 'current'


def test_missing_grant_is_installed_through_bulk(monkeypatch):
    monkeypatch.setattr('provisioning.gmail_login.load_gmail_token', lambda: {
        'client_id': 'example', 'client_secret': 'private-secret', 'refresh_token': 'private-token'})
    class Client:
        def __init__(self): self.installed = False
        def prepare_gmail_grant(self): return {'status': 'current' if self.installed else 'missing'}
        def bulk_update_items(self, items):
            assert len(items) == 1 and items[0]['operation'] == 'install_gmail_grant'
            self.installed = True
            return {'failed': 0, 'results': [{'status': 'succeeded', 'result': {'status': 'installed'}}]}
    result = execute(Client())
    assert result['status'] == 'current'
    assert 'private' not in str(result)


def test_failed_bulk_ack_is_a_workflow_failure():
    from services.application_service_client import BulkUpdateError
    import pytest
    class Client:
        def prepare_gmail_grant(self):
            return {'status': 'due', 'refresh_expires_at': 10, 'access_token': 'secret',
                    'message': {'to': 'ada@example.com', 'subject': 'renew', 'body': 'renew'}}
        def bulk_update_items(self, items):
            return {'failed': 1, 'results': [{'status': 'reconciliation_required'}]}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'{"id":"sent"}'
    with pytest.raises(BulkUpdateError): execute(Client(), opener=lambda *a, **kw: Response())
