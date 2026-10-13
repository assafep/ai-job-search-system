from services.workload_executor import plan_persistence_writes
from persistence_api.repository import MemoryRepository


def test_application_package_does_not_mark_submitted_by_default():
    operations = plan_persistence_writes(
        "discover-and-apply",
        {"workload_id": "work-1", "application_id": "app-1"},
        {"application": {"cover_letter": "Hello", "proposed_action": "request_review"}},
    )
    assert not any(item.get("target") == "Applied" for item in operations)


def test_confirmed_external_submission_plans_applied_transition():
    operations = plan_persistence_writes(
        "discover-and-apply",
        {
            "workload_id": "work-2",
            "application_id": "app-1",
            "submission_confirmed": True,
            "existing_application_package": True,
            "existing_application_package_reference": "https://drive.google.com/file/d/package/view",
        },
        {"application": {"cover_letter": "Hello", "proposed_action": "submitted"}},
    )
    transitions = [item for item in operations if item.get("operation") == "transition"]
    assert transitions[0]["target"] == "Applied"


def test_replaying_current_state_is_a_noop():
    repository = MemoryRepository()
    application = repository.create_application("Example", "Backend", "https://example.test/job")
    repository.transition(application.application_id, "Bookmarked", idempotency_key="retry-1")
    assert repository.list_events(application.application_id)[0].data["bullets"] == [
        "Posting URL: https://example.test/job"
    ]


def test_transition_recovers_if_failure_happens_after_event_persistence():
    class FailingOnceRepository(MemoryRepository):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def append_event(self, application_id, event_type, data=None, idempotency_key=None):
            event = super().append_event(application_id, event_type, data, idempotency_key)
            if event_type == "status_changed" and self.fail_once:
                self.fail_once = False
                raise RuntimeError("simulated crash after event write")
            return event

    repository = FailingOnceRepository()
    application = repository.create_application("Example", "Backend", "https://example.test/retry")

    try:
        repository.transition(application.application_id, "Ready for Auto-Apply", idempotency_key="transition-1")
    except RuntimeError:
        pass
    else:
        raise AssertionError("the simulated failure should be raised")

    recovered = repository.transition(
        application.application_id, "Ready for Auto-Apply", idempotency_key="transition-1"
    )

    assert recovered.status == "Ready for Auto-Apply"
    status_events = [
        event for event in repository.list_events(application.application_id)
        if event.event_type == "status_changed"
    ]
    assert len(status_events) == 1
