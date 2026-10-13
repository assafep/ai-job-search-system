from services.workload_runner import main
from services.runtime_config import OnboardingRequired


def test_workload_runner_rejects_non_mapping_agent_results(tmp_path, capsys):
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"application_id": "app-1"}')
    results = tmp_path / "results.json"
    results.write_text('[{"briefing": "test"}]')

    code = main([
        "prepare-interview",
        "--fixture", str(fixture),
        "--agent-results", str(results),
    ])

    assert code == 1
    assert "keyed by agent name" in capsys.readouterr().out


def test_execute_reports_onboarding_command_when_runtime_is_missing(tmp_path, monkeypatch, capsys):
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"application_id": "app-1"}')
    results = tmp_path / "results.json"
    results.write_text('{"interview-prep": {"briefing": "test"}}')
    monkeypatch.setattr(
        "services.workload_runner.require_runtime_configuration",
        lambda: (_ for _ in ()).throw(OnboardingRequired("config/runtime.json is missing")),
    )

    code = main([
        "prepare-interview",
        "--fixture", str(fixture),
        "--agent-results", str(results),
        "--execute",
    ])

    assert code == 1
    output = capsys.readouterr().out
    assert "onboarding_command" in output
    assert "python3 -m services.onboarding" in output


def test_execute_rejects_partial_agent_results(tmp_path, capsys):
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"application_id": "app-1"}')
    results = tmp_path / "results.json"
    results.write_text('{}')

    code = main([
        "prepare-interview",
        "--fixture", str(fixture),
        "--agent-results", str(results),
        "--execute",
    ])

    assert code == 1
    assert "missing agent results: interview-prep" in capsys.readouterr().out
