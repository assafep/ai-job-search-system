import json

from services.approval_cli import main


def test_approval_cli_can_request_list_and_decide(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPROVAL_REGISTRY_PATH", str(tmp_path / "approvals.json"))

    assert main(["request", "send_recruitment_email", "work-cli", "--context", '{"x": 1}']) == 0
    request = json.loads(capsys.readouterr().out)
    assert request["status"] == "pending"

    assert main(["list"]) == 0
    pending = json.loads(capsys.readouterr().out)
    assert pending[0]["request_id"] == request["request_id"]

    assert main(["decide", request["request_id"], "approved", "--note", "looks good"]) == 0
    decided = json.loads(capsys.readouterr().out)
    assert decided["status"] == "approved"
