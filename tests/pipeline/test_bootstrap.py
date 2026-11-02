"""Local bootstrap does not contact a coding agent or Google."""
import json

from ajss.onboard.bootstrap import coding_agent_status, google_account_status, prepare_workspace


def test_workspace_is_ready_before_either_account_is_connected(tmp_path):
    environ = {}
    report = prepare_workspace(tmp_path, environ)
    assert report["ready_for_integration_tests"] is True
    assert report["google_account"] == "not connected"
    assert report["coding_agent"] == "not connected"
    assert (tmp_path / "ajss-gdtq.sqlite3").is_file()
    saved = json.loads((tmp_path / "integration-workspace.json").read_text(encoding="utf-8"))
    assert saved["tests"] == "python -m pytest -q tests/pipeline"


def test_visible_accounts_are_reported_and_not_called(tmp_path):
    calls = []

    def which(name):
        calls.append(name)
        return "/usr/bin/" + name if name == "grok" else None

    assert coding_agent_status({"CODING_AGENT": "grok"}, which) == "present:grok"
    assert calls == ["grok"]
    assert google_account_status({"GOOGLE_ACCESS_TOKEN": "present"}) == "present"
    assert google_account_status({}) == "not connected"
