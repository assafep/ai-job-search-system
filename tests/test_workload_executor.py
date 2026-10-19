from services.workload_executor import execute_persistence_writes, plan_persistence_writes


def test_interview_result_becomes_explicit_idempotent_event():
    context = {"workload_id": "work-1", "application_id": "tab-1"}
    operations = plan_persistence_writes(
        "prepare-interview", context,
        {"interview-prep": {"briefing": "Ask about incident response."}},
    )
    assert operations[0]["operation"] == "transition"
    assert operations[1]["operation"] == "write_research"
    assert operations[0]["application_id"] == "tab-1"
    assert operations[0]["idempotency_key"]
    assert execute_persistence_writes(operations, None)["dry_run"] is True


def test_agent_result_is_not_executed_in_dry_run():
    class UnexpectedClient:
        def append_event(self, *args):
            raise AssertionError("dry-run must not call persistence")

    operations = plan_persistence_writes(
        "handle-incoming-email", {"workload_id": "work-2", "application_id": "tab-2"},
        {"correspondence": {"intent": "rejection", "proposed_action": "record rejection"}},
    )
    result = execute_persistence_writes(operations, UnexpectedClient())
    assert result["dry_run"] is True


def test_explicit_write_uses_persistence_client_when_enabled():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def append_event(self, *args):
            self.calls.append(args)
            return {"event_id": "evt-1"}

        def write_research(self, *args):
            self.calls.append(args)
            return {"research_tab_id": "research-1"}

        def transition(self, *args):
            self.calls.append(args)
            return {"status": args[1]}

    client = FakeClient()
    operations = plan_persistence_writes(
        "prepare-interview", {"workload_id": "work-3", "application_id": "tab-3"},
        {"interview-prep": {"briefing": "Discuss architecture."}},
    )
    result = execute_persistence_writes(operations, client, dry_run=False)
    assert result["results"] == [
        {"status": "Interviewing"},
        {"research_tab_id": "research-1"},
        {"event_id": "evt-1"},
    ]
    assert client.calls[0][0:2] == ("tab-3", "Interviewing")


def test_reconciliation_records_observation_and_optional_status():
    operations = plan_persistence_writes(
        "reconcile", {
            "workload_id": "work-reconcile", "application_id": "tab-4",
            "reconciliation_result": {
                "summary": "Recruiter confirmed next round",
                "external_status": "interviewing", "target_status": "Interviewing",
            },
        }, {},
    )
    assert [item["operation"] for item in operations] == ["append_event", "transition"]
    assert operations[0]["event_type"] == "reconciliation_completed"
    assert "External Status: interviewing" in operations[0]["data"]["bullets"]
    assert operations[1]["target"] == "Interviewing"


def test_scheduling_correspondence_draft_includes_calendly_link():
    operations = plan_persistence_writes(
        "handle-incoming-email", {
            "workload_id": "work-calendar-link", "application_id": "tab-5",
            "calendly_url": "https://calendly.com/example/intro",
        }, {"correspondence": {
            "intent": "reschedule_interview", "proposed_action": "draft_response",
            "draft": "Happy to find another time.",
        }},
    )
    assert "https://calendly.com/example/intro" in " ".join(
        operations[0]["data"]["bullets"]
    )


def test_another_meeting_alias_transitions_and_adds_link():
    operations = plan_persistence_writes(
        "handle-incoming-email", {
            "workload_id": "work-another-meeting", "application_id": "tab-6",
            "calendly_url": "https://calendly.com/example/intro",
        }, {"correspondence": {
            "intent": "request_another_meeting", "proposed_action": "draft_response",
        }},
    )
    assert operations[0]["data"]["bullets"][-1].endswith(
        "https://calendly.com/example/intro"
    )
    assert operations[1]["target"] == "Interviewing"


def test_package_always_checks_readiness_when_application_snapshot_is_absent():
    operations = plan_persistence_writes(
        "prepare-validated", {
            "workload_id": "work-resumed-package", "application_id": "tab-1",
            "cv_html": "<p>CV</p>",
        }, {"application": {
            "cover_letter": "Dear team", "proposed_action": "request_review",
        }},
    )
    assert operations[-1]["operation"] == "ensure_ready_to_apply"
    assert operations[-1]["application_id"] == "tab-1"


def test_package_failure_cannot_promote_application():
    calls = []

    class FailingClient:
        def get_application(self, application_id):
            return {"application_id": application_id, "status": "Bookmarked"}

        def store_artifact(self, *args):
            calls.append("artifact")
            raise RuntimeError("artifact storage failed")

        def transition(self, *args):
            calls.append("transition")
            return {"status": "Prepared"}

    operations = plan_persistence_writes(
        "prepare-validated",
        {"workload_id": "work-package-failure", "application_id": "tab-1", "cv_html": "<p>CV</p>"},
        {"application": {"cover_letter": "Dear team", "proposed_action": "request_review"}},
    )

    try:
        execute_persistence_writes(operations, FailingClient(), dry_run=False)
    except RuntimeError as error:
        assert str(error) == "artifact storage failed"
    else:
        raise AssertionError("package failure should fail the workload")
    assert calls == ["artifact"]


def test_application_agent_can_hold_weak_match_in_bookmarked():
    operations = plan_persistence_writes(
        "prepare-validated",
        {"workload_id": "work-weak-match", "application_id": "tab-1"},
        {"application": {
            "cover_letter": "Dear team",
            "proposed_action": "Keep application Bookmarked pending human review",
        }},
    )

    # No success event or promotion without a successfully stored package.
    assert operations == []


def test_review_recommendation_cannot_block_saved_package_readiness():
    operations = plan_persistence_writes('prepare-validated', {
        'application_id': 'app', 'existing_application_package': True,
        'existing_application_package_reference': 'https://drive.google.com/file/d/package/view',
    }, {'application': {'cover_letter': 'Grounded cover letter',
                        'proposed_action': 'Keep Bookmarked pending human review'}})
    assert operations[-1]['operation'] == 'ensure_ready_to_apply'
    assert 'human review' not in str(operations[0]['data']['bullets']).lower()




def test_ensure_ready_to_apply_rejects_missing_application():
    class MissingClient:
        def get_application(self, application_id):
            return None

    try:
        execute_persistence_writes([{
            "operation": "ensure_ready_to_apply", "application_id": "missing",
        }], MissingClient(), dry_run=False)
    except KeyError as error:
        assert error.args == ("missing",)
    else:
        raise AssertionError("missing application should fail explicitly")
