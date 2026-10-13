import base64
import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from adapters.google_events import GoogleEventError, GoogleEventSource, body_text
from services.external_event_polling import matching_application, poll
from services.workload_registry import WorkloadRegistry


def response(value):
    return BytesIO(json.dumps(value).encode())


def test_google_permission_failure_is_safe_and_actionable():
    def opener(req, **kwargs):
        assert req.method == 'GET'
        raise HTTPError(req.full_url, 403, 'Forbidden', {}, response({'error': {'errors': [{'reason': 'insufficientPermissions'}]}}))
    with pytest.raises(GoogleEventError) as caught:
        GoogleEventSource('secret-token', opener).calendar()
    assert caught.value.status == 403
    assert caught.value.reason == 'insufficientPermissions'
    assert 'secret-token' not in str(caught.value)


def test_message_decoding_ignores_attachments():
    def part(text, **kwargs):
        return dict(mimeType='text/plain', body={'data': base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')}, **kwargs)
    assert body_text({'parts': [part('Interview invitation'), part('private attachment', filename='private.txt')]}) == 'Interview invitation'


def test_gmail_pagination_gets_bodies_and_uses_inbound_company_filter():
    calls = []
    def opener(req, **kwargs):
        from urllib.parse import parse_qs, urlsplit
        calls.append(req)
        query = parse_qs(urlsplit(req.full_url).query)
        if urlsplit(req.full_url).path.endswith('/messages'):
            assert '-in:sent' in query['q'][0] and 'Example' in query['q'][0]
            if 'pageToken' not in query:
                return response({'messages': [{'id': '1'}], 'nextPageToken': 'page-2'})
            return response({'messages': [{'id': '2'}]})
        return response({'id': str(len(calls)), 'payload': {'headers': [{'name': 'Subject', 'value': 'Example interview'}]}})
    values, truncated = GoogleEventSource('token', opener).gmail(['Example'], limit=3)
    assert len(values) == 2 and not truncated
    assert len(calls) == 4
    assert all(req.method == 'GET' for req in calls)


def test_calendar_cancelled_events_excluded_and_revision_event_id_changes():
    def opener(req, **kwargs):
        assert 'timeMin=' in req.full_url and 'timeMax=' in req.full_url
        return response({'items': [{'id': 'cancel', 'status': 'cancelled'},
                                   {'id': 'meeting', 'updated': 'revision', 'summary': 'Example interview', 'start': {'dateTime': 'start'}}]})
    events, truncated = GoogleEventSource('token', opener).calendar()
    assert not truncated and len(events) == 1
    assert events[0]['event_id'] == 'meeting:revision'
    assert events[0]['start'] == 'start'


def test_ambiguous_application_matches_are_not_guessed():
    apps = [{'company': 'Example', 'role': 'Backend', 'application_id': 'one'},
            {'company': 'Example', 'role': 'Frontend', 'application_id': 'two'}]
    assert matching_application('Example interview', apps) is None
    assert matching_application('Example Backend interview', apps)['application_id'] == 'one'
    assert matching_application('Examples interview', apps) is None


class Persistence:
    def __init__(self): self.saved = {}
    def list_applications(self):
        return [{'company': 'Example', 'role': 'Backend', 'application_id': 'app', 'status': 'Applied'}]
    def store_file(self, path, content, mime, key): self.saved[path] = json.loads(content)


def test_poll_routes_real_events_and_repoll_is_idempotent():
    class Source:
        def gmail(self, companies):
            return [{'message_id': 'msg', 'subject': 'Example Backend interview', 'body': 'Invitation'},
                    {'message_id': 'news', 'subject': 'Example newsletter', 'body': 'Promotion'}], False
        def calendar(self):
            return [{'event_id': 'meeting:v1', 'title': 'Example interview', 'start': 'date'}], False
    registry = WorkloadRegistry()
    persistence = Persistence()
    result = poll(persistence, registry, Source())
    assert len(registry.records) == 2
    assert result['handle-incoming-email']['queued'] == 1
    assert result['prepare-interview']['queued'] == 1
    result = poll(persistence, registry, Source())
    assert result['handle-incoming-email']['already_known'] == 1
    assert len(registry.records) == 2
    assert all('/event-polling/latest.json' in path for path in persistence.saved)


def test_one_blocked_source_does_not_prevent_the_other_from_polling():
    class Source:
        def gmail(self, companies): raise GoogleEventError('gmail', 403, 'insufficientPermissions')
        def calendar(self): return [], False
    result = poll(Persistence(), WorkloadRegistry(), Source())
    assert result['handle-incoming-email']['status'] == 'blocked'
    assert result['handle-incoming-email']['required_scope'].endswith('gmail.readonly')
    assert result['prepare-interview']['status'] == 'ok'
