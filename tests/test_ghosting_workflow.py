from datetime import datetime, timedelta, timezone

from services.ghosting_workflow import execute, ghosting_reason


NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def test_silence_is_ghosted_for_applied_and_expired_for_interviewing():
    old = (NOW - timedelta(days=14)).isoformat()
    recent = (NOW - timedelta(days=13)).isoformat()
    applied = {'status': 'Applied', 'updated_at': old, 'created_at': old}
    interviewing = {'status': 'Interviewing', 'updated_at': old, 'created_at': old}
    assert ghosting_reason(applied, [], NOW) == 'ghosted'
    assert ghosting_reason(interviewing, [], NOW) == 'expired'
    assert ghosting_reason(applied, [{'occurred_at': recent}], NOW) is None
    assert ghosting_reason({'status': 'Prepared', 'updated_at': old}, [], NOW) is None


class Client:
    def __init__(self):
        self.completed = []
        self.applications = [
            {'application_id': 'old', 'status': 'Applied', 'updated_at': '2026-10-01T00:00:00+00:00'},
            {'application_id': 'live', 'status': 'Interviewing', 'updated_at': '2026-10-20T00:00:00+00:00'},
            {'application_id': 'quiet-interview', 'status': 'Interviewing', 'updated_at': '2026-10-01T00:00:00+00:00'},
        ]

    def list_applications(self):
        return self.applications

    def list_events(self, application_id):
        if application_id == 'quiet-interview':
            return [{'occurred_at': '2026-10-02T00:00:00+00:00', 'data': {}}]
        return []

    def bulk_update_items(self, items, batch_size=25):
        assert batch_size == 5
        self.items = items
        for item in items:
            assert item['operation'] == 'complete_application'
            assert 'inactive_before' in item['data']
            self.completed.append((item['application_id'], item['data']['reason']))
        return {'failed': 0, 'results': [{'status': 'succeeded'} for _ in items]}


def test_workflow_completes_only_the_silent_matches():
    client = Client()
    result = execute(client, now=NOW)
    assert client.completed == [('old', 'ghosted'), ('quiet-interview', 'expired')]
    assert result['workflow'] == 'identify-ghosting'


def test_ghosting_item_keys_are_stable_across_poll_times():
    first, second = Client(), Client()
    execute(first, now=NOW)
    execute(second, now=NOW + timedelta(hours=1))
    assert first.items == second.items


def test_bulk_failure_is_not_reported_as_a_completed_workflow():
    import pytest
    from services.application_service_client import BulkUpdateError
    client = Client()
    client.bulk_update_items = lambda *args, **kwargs: {'failed': 1, 'results': [{'status': 'failed'}]}
    with pytest.raises(BulkUpdateError): execute(client, now=NOW)
