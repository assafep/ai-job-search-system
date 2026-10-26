from services.workload_executor import plan_persistence_writes


def test_rejection_correspondence_plans_rejected_transition():
    operations = plan_persistence_writes(
        "handle-incoming-email",
        {"workload_id": "work-rejection", "application_id": "tab-1"},
        {"correspondence": {
            "intent": "rejection",
            "proposed_action": "record rejection",
        }},
    )

    transition = [item for item in operations if item["operation"] == "transition"]
    assert transition[0]["target"] == "Rejected"


def test_apply_confirmation_plans_applied_transition():
    operations = plan_persistence_writes(
        "handle-incoming-email",
        {"workload_id": "work-confirmation", "application_id": "tab-1"},
        {"correspondence": {
            "intent": "application_received",
            "proposed_action": "record confirmation",
        }},
    )
    transition = [item for item in operations if item["operation"] == "transition"]
    assert transition[0]["target"] == "Applied"
