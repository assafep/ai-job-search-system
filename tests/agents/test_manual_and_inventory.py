"""Ensure every configured workflow role has an explicit test owner."""
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from ajss.worker.manual import manual_transition
from ajss.queue.policy import RoutingError

COVERAGE = {
    "bookmark_discovered":"test_pipeline_agents.py",
    "validate_bookmarked":"test_pipeline_agents.py",
    "prepare_validated":"test_pipeline_agents.py",
    "submit_prepared":"test_pipeline_agents.py",
    "email_correspondence":"test_observer_agents.py",
    "calendar_observer":"test_observer_agents.py",
    "research":"test_observer_agents.py",
    "manual_update":"test_manual_and_inventory.py",
    "phone_followup":"test_manual_and_inventory.py",
}


def test_all_configured_workflow_agents_have_test_owners():
    root=Path(__file__).resolve().parents[2]
    routes=json.loads((root/"config/gdtq-routing.json").read_text())["routes"]
    assert set(routes)==set(COVERAGE)
    assert all((Path(__file__).parent/name).is_file() for name in COVERAGE.values())


@pytest.mark.parametrize("source,agent,targets", [
    ("Manual Apply Required","manual_update",{"Applied":"APPLIED","Interviewing":"INTERVIEWING","Rejected":"REJECTED","Offer":"OFFER","Prepared":"PREPARED","Duplicate":"DUPLICATE"}),
    ("Applied","phone_followup",{"Interviewing":"INTERVIEWING","Rejected":"REJECTED","Offer":"OFFER"}),
    ("Interviewing","phone_followup",{"Interviewing":"INTERVIEWING","Rejected":"REJECTED","Offer":"OFFER"}),
])
def test_manual_agent_emits_exact_payload_to_mock_orchestrator(source,agent,targets):
    for target,code in targets.items():
        orchestrator=Mock(spec_set=["engine","handle_external"])
        orchestrator.engine.transitions={source:set(targets)}
        orchestrator.handle_external.return_value="accepted"
        assert manual_transition(orchestrator,source,"item-1",target,reason="Fixture update")=="accepted"
        orchestrator.handle_external.assert_called_once_with(source,"item-1",{
            "agent":agent,"item_id":"item-1","code":code,"execution_status":"SUCCESS",
            "summary":"Fixture update","data":{"manual":True}})


def test_forbidden_manual_transition_has_no_effect():
    orchestrator=Mock(spec_set=["engine","handle_external"])
    orchestrator.engine.transitions={"Applied":{"Interviewing"}}
    with pytest.raises(RoutingError):manual_transition(orchestrator,"Applied","item-1","Prepared")
    orchestrator.handle_external.assert_not_called()
