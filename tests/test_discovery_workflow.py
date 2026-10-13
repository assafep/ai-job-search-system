from services.workload_executor import execute_persistence_writes, plan_persistence_writes


def test_discovery_candidate_becomes_bookmarked_application_operation():
    operations = plan_persistence_writes(
        "discover-and-apply",
        {"workload_id": "discovery-1"},
        {"job-discovery": {"candidates": [{
            "company": "Example",
            "role": "Backend Engineer",
            "posting_url": "https://example.test/job",
            "relevance_evidence": ["backend", "senior"],
        }]}},
    )
    assert operations[0]["operation"] == "create_application"
    assert operations[0]["posting_url"] == "https://example.test/job"


def test_discovery_create_operation_uses_persistence_client():
    calls = []

    class FakeClient:
        def create_application(self, *args):
            calls.append(args)
            return {"application_id": "app-1", "status": "Bookmarked"}

    operations = [{
        "operation": "create_application",
        "company": "Example",
        "role": "Backend",
        "posting_url": "https://example.test/job",
        "data": {},
        "idempotency_key": "discovery-1",
    }]
    result = execute_persistence_writes(operations, FakeClient(), dry_run=False)
    assert result["results"] == [{"application_id": "app-1", "status": "Bookmarked"}]
    assert calls[0][2] == "https://example.test/job"
    assert calls[0][4] == "discovery-1"
