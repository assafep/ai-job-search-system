from services.workload_executor import execute_persistence_writes, plan_persistence_writes


def test_discovery_creates_discovered_application_operation():
    operations = plan_persistence_writes(
        "discover",
        {
            "workload_id": "discovery-1",
            "job_postings": [{
                "company": "Example",
                "role": "Backend Engineer",
                "posting_url": "https://example.test/job",
            }],
        },
        {},
    )
    assert operations[0]["operation"] == "create_application"
    assert operations[0]["posting_url"] == "https://example.test/job"
    assert operations[0]["target_state"] == "Discovered"


def test_discovery_create_operation_uses_persistence_client():
    calls = []

    class FakeClient:
        def create_application(self, *args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return {"application_id": "app-1", "status": "Discovered"}

    operations = [{
        "operation": "create_application",
        "company": "Example",
        "role": "Backend",
        "posting_url": "https://example.test/job",
        "data": {},
        "target_state": "Discovered",
        "idempotency_key": "discovery-1",
    }]
    result = execute_persistence_writes(operations, FakeClient(), dry_run=False)
    assert calls[0]["args"][2] == "https://example.test/job"
    assert calls[0]["kwargs"]["target_state"] == "Discovered"
    assert calls[0]["kwargs"]["idempotency_key"] == "discovery-1"


def test_validate_match_creates_event_and_transition():
    operations = plan_persistence_writes(
        "validate-bookmarked",
        {
            "workload_id": "validate-1",
            "application_id": "app-1",
        },
        {"score": {
            "company": "Example",
            "role": "Backend Engineer",
            "posting_url": "https://example.test/job",
            "is_match": True,
            "relevance_evidence": ["backend", "senior"],
        }},
    )
    assert operations[0]["operation"] == "append_event"
    assert operations[0]["event_type"] == "posting_validated"
    assert "Validated: strong match for applicant" in operations[0]["data"]["bullets"]
    assert operations[1]["operation"] == "transition"
    assert operations[1]["target"] == "Validated"


def test_validate_decline_creates_event_and_transition():
    operations = plan_persistence_writes(
        "validate-bookmarked",
        {
            "workload_id": "validate-1",
            "application_id": "app-1",
        },
        {"score": {
            "company": "Example",
            "role": "Backend Engineer",
            "posting_url": "https://example.test/job",
            "is_match": False,
            "decline_reason": "Not enough experience",
        }},
    )
    assert operations[0]["operation"] == "append_event"
    assert operations[0]["event_type"] == "posting_invalidated"
    assert "Invalidated: Not enough experience" in operations[0]["data"]["bullets"]
    assert operations[1]["operation"] == "transition"
    assert operations[1]["target"] == "Invalidated"


def test_discovery_filters_by_location_match():
    operations = plan_persistence_writes(
        "discover",
        {
            "workload_id": "discovery-1",
            "applicant_profile": {"job_search": {"countries": ["IL"]}},
            "job_postings": [
                {
                    "company": "Israel Tech",
                    "role": "Backend Engineer",
                    "posting_url": "https://example.test/il-job",
                    "countries": ["IL"],
                },
                {
                    "company": "US Tech",
                    "role": "Backend Engineer",
                    "posting_url": "https://example.test/us-job",
                    "countries": ["US"],
                },
            ],
        },
        {},
    )
    urls = [op["posting_url"] for op in operations]
    assert "https://example.test/il-job" in urls
    assert "https://example.test/us-job" not in urls


def test_discovery_filters_by_location_case_insensitive():
    operations = plan_persistence_writes(
        "discover",
        {
            "workload_id": "discovery-1",
            "applicant_profile": {"job_search": {"countries": ["il"]}},
            "job_postings": [
                {
                    "company": "Israel Tech",
                    "role": "Backend Engineer",
                    "posting_url": "https://example.test/il-job",
                    "countries": ["IL"],
                },
            ],
        },
        {},
    )
    assert len(operations) == 1


def test_discovery_skips_posting_with_no_country_when_preferred_set():
    operations = plan_persistence_writes(
        "discover",
        {
            "workload_id": "discovery-1",
            "applicant_profile": {"job_search": {"countries": ["IL"]}},
            "job_postings": [
                {
                    "company": "Unknown Tech",
                    "role": "Backend Engineer",
                    "posting_url": "https://example.test/no-country-job",
                    "countries": [],
                },
            ],
        },
        {},
    )
    assert len(operations) == 0


def test_discovery_allows_posting_without_country_when_no_preference():
    operations = plan_persistence_writes(
        "discover",
        {
            "workload_id": "discovery-1",
            "applicant_profile": {"job_search": {"countries": []}},
            "job_postings": [
                {
                    "company": "Unknown Tech",
                    "role": "Backend Engineer",
                    "posting_url": "https://example.test/no-country-job",
                    "countries": [],
                },
            ],
        },
        {},
    )
    assert len(operations) == 1


def test_discovery_allows_posting_without_country_field_when_no_preference():
    operations = plan_persistence_writes(
        "discover",
        {
            "workload_id": "discovery-1",
            "applicant_profile": {"job_search": {"countries": []}},
            "job_postings": [
                {
                    "company": "Unknown Tech",
                    "role": "Backend Engineer",
                    "posting_url": "https://example.test/no-country-field",
                },
            ],
        },
        {},
    )
    assert len(operations) == 1


def test_discovery_multi_country_match():
    operations = plan_persistence_writes(
        "discover",
        {
            "workload_id": "discovery-1",
            "applicant_profile": {"job_search": {"countries": ["IL", "US"]}},
            "job_postings": [
                {
                    "company": "Global Tech",
                    "role": "Backend Engineer",
                    "posting_url": "https://example.test/global-job",
                    "countries": ["US", "GB"],
                },
            ],
        },
        {},
    )
    assert len(operations) == 1
