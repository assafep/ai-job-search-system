import pytest

from adapters.contracts import calendar_event, incoming_email, job_discovery_tick, reconciliation_tick


def test_adapter_contracts_produce_dispatchable_events():
    assert calendar_event({"event_id": "cal-1"})["event_type"] == "calendar_event"
    assert incoming_email({"message_id": "mail-1"})["event_type"] == "incoming_email"
    assert job_discovery_tick({"event_id": "jobs-1"})["event_type"] == "job_discovery_schedule"
    assert reconciliation_tick({"event_id": "reconcile-1"})["event_type"] == "reconciliation_schedule"


@pytest.mark.parametrize("normalizer, field", [
    (calendar_event, "event_id"),
    (incoming_email, "message_id"),
    (job_discovery_tick, "event_id"),
    (reconciliation_tick, "event_id"),
])
def test_adapter_contracts_require_source_identity(normalizer, field):
    with pytest.raises(ValueError, match=field):
        normalizer({})
