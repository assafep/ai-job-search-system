from services.approval_service import decision, evaluate


def test_default_is_review():
    assert decision("unknown_action", {}) == "ask"


def test_policy_is_applied():
    assert decision("submit_job_application", {"submit_job_application": "ask"}) == "ask"
    assert evaluate(["submit_job_application", "send_recruitment_email"], {
        "submit_job_application": "ask",
        "send_recruitment_email": "deny",
    }) == [
        {"action": "submit_job_application", "decision": "ask"},
        {"action": "send_recruitment_email", "decision": "deny"},
    ]

