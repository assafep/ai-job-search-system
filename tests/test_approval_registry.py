from services.approval_registry import ApprovalRegistry, approval_registry_from_environment


def test_approval_registry_is_idempotent_and_persistent(tmp_path):
    path = str(tmp_path / "approvals.json")
    registry = ApprovalRegistry(path)
    first = registry.request("submit_job_application", "work-1", {"application_id": "app-1"}, "approval-1")
    retry = registry.request("submit_job_application", "work-1", {"changed": True}, "approval-1")

    assert retry.request_id == first.request_id
    assert len(registry.pending()) == 1
    assert ApprovalRegistry(path).pending()[0].request_id == first.request_id


def test_approval_registry_decides_and_reports(tmp_path):
    registry = ApprovalRegistry(str(tmp_path / "approvals.json"))
    request = registry.request("send_recruitment_email", "work-2")

    decided = registry.decide(request.request_id, "approved", "Reviewed by user")

    assert decided.status == "approved"
    assert registry.report()["counts"]["approved"] == 1
    assert registry.report()["pending"] == []


def test_approval_registry_environment_factory(monkeypatch, tmp_path):
    path = str(tmp_path / "approvals.json")
    monkeypatch.setenv("APPROVAL_REGISTRY_PATH", path)
    assert approval_registry_from_environment().storage_path == path
