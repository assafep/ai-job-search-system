from persistence_api.repository import MemoryRepository
from services.event_dispatcher import dispatch
from services.workload_registry import WorkloadRegistry
from services.workflow_runtime import WorkflowRuntime


def applied_application(repository):
    application = repository.create_application(
        "Example", "Backend", "https://example.test/job"
    )
    repository.transition(application.application_id, "Prepared")
    repository.transition(application.application_id, "Applied")
    return application


def test_interview_preparation_runtime_persists_research_and_state():
    repository = MemoryRepository()
    application = applied_application(repository)
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch(
        "calendar_event",
        {"application_id": application.application_id},
        "interview-e2e-1",
    ))

    result = WorkflowRuntime(registry, repository).run(
        record.workload_id,
        agent_provider=lambda prompt, context: {
            "briefing": "Ask about incident response and ownership."
        },
    )

    assert result["status"] == "succeeded"
    assert repository.get_application(application.application_id).status == "Interviewing"
    assert repository.list_research(application.application_id)[0]["title"] == (
        "Research — Interview Briefing"
    )


def test_rejection_correspondence_runtime_updates_application_state():
    repository = MemoryRepository()
    application = applied_application(repository)
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch(
        "incoming_email",
        {"application_id": application.application_id, "message_id": "message-1"},
        "correspondence-e2e-1",
    ))

    result = WorkflowRuntime(registry, repository).run(
        record.workload_id,
        agent_provider=lambda prompt, context: {
            "intent": "rejection",
            "proposed_action": "record rejection",
        },
    )

    assert result["status"] == "succeeded"
    assert repository.get_application(application.application_id).status == "Rejected"
    assert any(
        event.event_type == "correspondence_classified"
        for event in repository.list_events(application.application_id)
    )


def test_offer_correspondence_runtime_reaches_offer_from_interviewing():
    repository = MemoryRepository()
    application = applied_application(repository)
    repository.transition(application.application_id, "Interviewing")
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch(
        "incoming_email",
        {"application_id": application.application_id, "message_id": "message-offer-1"},
        "correspondence-offer-e2e-1",
    ))

    result = WorkflowRuntime(registry, repository).run(
        record.workload_id,
        agent_provider=lambda prompt, context: {
            "intent": "offer",
            "proposed_action": "record offer",
        },
    )

    assert result["status"] == "succeeded"
    assert repository.get_application(application.application_id).status == "Offer"


def test_correspondence_uses_legacy_personal_calendly_profile_field():
    repository = MemoryRepository()
    repository.save_profile({
        "schema_version": 1,
        "personal": {"calendly_url": "https://calendly.com/legacy/intro"},
        "professional": {},
        "employment": [],
    })
    application = applied_application(repository)
    registry = WorkloadRegistry()
    record = registry.enqueue(dispatch(
        "incoming_email",
        {"application_id": application.application_id, "message_id": "message-schedule-legacy"},
        "correspondence-legacy-calendly-1",
    ))

    result = WorkflowRuntime(registry, repository).run(
        record.workload_id,
        agent_provider=lambda prompt, context: {
            "intent": "request_interview",
            "proposed_action": "send scheduling link",
            "draft": "Please choose a time.",
        },
    )

    assert result["status"] == "succeeded"
    event = next(
        event for event in repository.list_events(application.application_id)
        if event.event_type == "correspondence_classified"
    )
    assert any("https://calendly.com/legacy/intro" in bullet for bullet in event.data["bullets"])
