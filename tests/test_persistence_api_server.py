import json
import threading
import urllib.error
import urllib.request

from persistence_api import main
from persistence_api.repository import MemoryRepository
from services.event_dispatcher import dispatch
from services.persistence_client import PersistenceApiClient
from services.workflow_runtime import WorkflowRuntime
from services.workload_registry import WorkloadRegistry


def test_persistence_api_server_round_trip(monkeypatch, candidate_profile):
    monkeypatch.setattr(main, "repository", MemoryRepository())
    monkeypatch.setenv("PERSISTENCE_API_TOKEN", "integration-token")
    server = main.HTTPServer(("127.0.0.1", 0), main.Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    base_url = "http://127.0.0.1:{}".format(server.server_address[1])
    client = PersistenceApiClient(base_url, auth_token="integration-token")
    client.save_profile(candidate_profile)

    def request(method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = {"Authorization": "Bearer integration-token"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            base_url + path, data=body, headers=request_headers, method=method
        )
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    try:
        status, health = request("GET", "/health")
        assert status == 200 and health == {"ok": True}
        assert client.journal_status()["valid"] is True
        assert client.repair_journal()["missing_states"] == []

        application = client.create_application(
            "Integration Co",
            "Backend Engineer",
            "https://example.test/integration",
            idempotency_key="integration-create",
        )
        application_id = application["application_id"]

        updated = client.transition(
            application_id, "Ready for Auto-Apply", idempotency_key="integration-transition"
        )
        assert updated["status"] == "Ready for Auto-Apply"

        event = client.append_event(
            application_id, "integration_note", {"bullets": ["Connected"]}
        )
        assert event["event_type"] == "integration_note"

        stored_file = client.store_file("integration/notes.txt", b"connected", "text/plain")
        assert stored_file["path"] == "integration/notes.txt"
        assert client.read_file("integration/notes.txt")["content"] == b"connected"

        workflow_application = client.create_application(
            "Workflow Co", "Platform Engineer", "https://example.test/workflow"
        )
        workflow_id = workflow_application["application_id"]
        client.transition(workflow_id, "Ready for Auto-Apply")
        client.transition(workflow_id, "Applied")
        registry = WorkloadRegistry()
        record = registry.enqueue(dispatch(
            "calendar_event", {"application_id": workflow_id}, "integration-calendar-1"
        ))
        workflow_result = WorkflowRuntime(registry, client).run(
            record.workload_id,
            agent_provider=lambda prompt, context: {
                "briefing": "Ask about ownership and incident response."
            },
        )
        assert workflow_result["status"] == "succeeded"
        assert client.get_application(workflow_id)["status"] == "Interviewing"
        assert client.list_research(workflow_id)[0]["title"] == (
            "Research — Interview Briefing"
        )

        rejection_application = client.create_application(
            "Rejection Co", "Backend Engineer", "https://example.test/rejection"
        )
        rejection_id = rejection_application["application_id"]
        client.transition(rejection_id, "Ready for Auto-Apply")
        client.transition(rejection_id, "Applied")
        rejection_registry = WorkloadRegistry()
        rejection_record = rejection_registry.enqueue(dispatch(
            "incoming_email", {"application_id": rejection_id, "message_id": "integration-rejection"},
            "integration-rejection-workload",
        ))
        rejection_result = WorkflowRuntime(rejection_registry, client).run(
            rejection_record.workload_id,
            agent_provider=lambda prompt, context: {
                "intent": "rejection", "proposed_action": "record rejection"
            },
        )
        assert rejection_result["status"] == "succeeded"
        assert client.get_application(rejection_id)["status"] == "Rejected"

        offer_application = client.create_application(
            "Offer Co", "Platform Engineer", "https://example.test/offer"
        )
        offer_id = offer_application["application_id"]
        client.transition(offer_id, "Ready for Auto-Apply")
        client.transition(offer_id, "Applied")
        client.transition(offer_id, "Interviewing")
        offer_registry = WorkloadRegistry()
        offer_record = offer_registry.enqueue(dispatch(
            "incoming_email", {"application_id": offer_id, "message_id": "integration-offer"},
            "integration-offer-workload",
        ))
        offer_result = WorkflowRuntime(offer_registry, client).run(
            offer_record.workload_id,
            agent_provider=lambda prompt, context: {
                "intent": "offer", "proposed_action": "record offer"
            },
        )
        assert offer_result["status"] == "succeeded"
        assert client.get_application(offer_id)["status"] == "Offer"

        discovery_registry = WorkloadRegistry()
        discovery_record = discovery_registry.enqueue(dispatch(
            "job_discovery_bookmark_schedule", {
                "source_names": ["integration-source"],
                "job_postings": [{
                    "posting_url": "https://example.test/discovered",
                    "source_url": "https://example.test/discovered",
                    "description": "Senior backend engineer role. " * 30,
                }],
            },
            "integration-discovery-workload",
        ))

        def discovery_provider(prompt, context):
            return {"candidates": [{
                "company": "Discovery Co",
                "role": "Senior Backend Engineer",
                "posting_url": "https://example.test/discovered",
            }]}

        discovery_result = WorkflowRuntime(discovery_registry, client).run(
            discovery_record.workload_id, agent_provider=discovery_provider
        )
        assert discovery_result["status"] == "succeeded"
        discovered = client.list_applications(status="Bookmarked")
        assert any(item["posting_url"] == "https://example.test/discovered" for item in discovered)

        unauthenticated = urllib.request.Request(base_url + "/applications")
        try:
            urllib.request.urlopen(unauthenticated)
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("state endpoint accepted an unauthenticated request")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
