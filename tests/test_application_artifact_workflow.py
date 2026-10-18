from services.workload_executor import execute_persistence_writes, plan_persistence_writes
from services.workload_runner import execute


def test_application_result_plans_combined_pdf_when_cv_is_available():
    operations = plan_persistence_writes(
        "prepare-bookmarked",
        {
            "workload_id": "work-application",
            "application_id": "tab-1",
            "cv_html": "<p>Senior backend engineer</p>",
        },
        {"application": {
            "cover_letter": "Dear team, I build reliable systems.",
            "proposed_action": "request_review",
        }},
    )

    assert operations[0]["operation"] == "generate_and_store_artifact"
    assert operations[0]["name"] == "Applicant Cover Letter & Resume - Application - Package.pdf"
    assert operations[-1]["operation"] == "ensure_ready_to_apply"


def test_existing_bookmarked_application_is_promoted_before_package():
    operations = plan_persistence_writes(
        "prepare-bookmarked",
        {
            "workload_id": "work-bookmarked-application",
            "application_id": "tab-1",
            "application": {"status": "Bookmarked"},
            "cv_html": "<p>CV</p>",
        },
        {"application": {"cover_letter": "Dear team", "proposed_action": "request_review"}},
    )

    assert operations[-1]["operation"] == "ensure_ready_to_apply"
    assert operations[-1]["application_id"] == "tab-1"


def test_application_pdf_is_stored_only_when_execution_is_enabled():
    calls = []

    class FakeClient:
        def store_artifact(self, *args):
            calls.append(args)
            return {"artifact_id": "artifact-1"}

    operations = [{
        "operation": "generate_and_store_artifact",
        "application_id": "tab-1",
        "name": "application-package.pdf",
        "cover_letter": "Dear team",
        "cv_html": "<p>Backend engineer</p>",
        "mime_type": "application/pdf",
        "idempotency_key": "pdf-1",
    }]
    dry = execute_persistence_writes(operations, FakeClient())
    assert dry["dry_run"] is True
    assert not calls
    live = execute_persistence_writes(operations, FakeClient(), dry_run=False)
    assert live["results"] == [{"artifact_id": "artifact-1"}]
    assert calls[0][0:2] == ("tab-1", "application-package.pdf")


def test_direct_execution_uses_profile_scheduling_default():
    captured = []

    class FakeClient:
        def get_profile(self):
            return {"application_defaults": {"calendly_url": "https://calendly.com/profile/intro"}}

        def get_application(self, application_id):
            return {"application_id": application_id, "status": "Ready for Auto-Apply"}

        def store_artifact(self, application_id, name, content, mime_type, idempotency_key):
            captured.append(content)
            return {"artifact_id": "artifact-2"}

        def append_event(self, *args):
            return {"event_id": "event-2"}

    result = execute(
        "prepare-bookmarked",
        {"workload_id": "work-profile-link", "application_id": "tab-1", "cv_html": "<p>CV</p>"},
        {"application": {"cover_letter": "Dear team", "proposed_action": "request_review"}},
        FakeClient(),
    )

    assert result["execution"]["results"][0]["artifact_id"] == "artifact-2"
    assert b"https://calendly.com/profile/intro" in captured[0]
