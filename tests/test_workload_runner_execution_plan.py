import json

from services.workload_runner import run


def test_workload_runner_includes_dry_run_persistence_operations(tmp_path):
    context = {"workload_id": "work-1", "application_id": "tab-1"}
    result = run(
        "prepare-interview",
        context,
        {"interview-prep": {"briefing": "Prepare a systems question."}},
    )

    assert result["persistence_operations"][0]["operation"] == "transition"
    assert result["side_effects"] == "disabled"
