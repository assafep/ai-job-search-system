import json

from services import journal_cli


class FakeClient:
    def __init__(self):
        self.repaired = False

    def journal_status(self):
        return {"valid": False, "missing_states": ["Offer"], "duplicate_states": []}

    def repair_journal(self):
        self.repaired = True
        return {"valid": True, "missing_states": [], "duplicate_states": []}


def test_journal_status_is_read_only(capsys):
    client = FakeClient()
    assert journal_cli.main(["status"], client) == 0
    assert client.repaired is False
    assert json.loads(capsys.readouterr().out)["missing_states"] == ["Offer"]


def test_journal_repair_requires_apply(capsys):
    client = FakeClient()
    assert journal_cli.main(["repair"], client) == 0
    assert client.repaired is False
    output = json.loads(capsys.readouterr().out)
    assert output["repair_not_applied"] is True
    assert output["proposed_operations"] == [
        {"operation": "add_top_level_state_tab", "title": "Offer"}
    ]


def test_journal_repair_apply_calls_client(capsys):
    client = FakeClient()
    assert journal_cli.main(["repair", "--apply"], client) == 0
    assert client.repaired is True
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_journal_repair_apply_reports_unresolved_duplicate(capsys):
    class DuplicateClient(FakeClient):
        def repair_journal(self):
            self.repaired = True
            return {
                "valid": False,
                "missing_states": [],
                "duplicate_states": ["Bookmarked"],
            }

    result = journal_cli.main(["repair", "--apply"], DuplicateClient())
    assert result == 2
    assert json.loads(capsys.readouterr().out)["duplicate_states"] == ["Bookmarked"]
