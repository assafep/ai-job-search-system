from services.workload_runner import main


def test_discovery_fixture_is_accepted(tmp_path, capsys):
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"trigger": {"type": "manual_test"}}')
    results = tmp_path / "results.json"
    results.write_text('{"job-discovery": {"candidates": []}}')

    assert main([
        "discover-and-apply",
        "--fixture", str(fixture),
        "--agent-results", str(results),
    ]) == 0
    assert "persistence_operations" in capsys.readouterr().out
