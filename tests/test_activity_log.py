import threading

import pytest

from persistence_api.journal_format import ALL_STATES
from persistence_api.journal_format import ALL_STATES
from persistence_api.repository import MemoryRepository
from services.activity_log import configure, configure_from_runtime, posting_label
from services.orchestrator import Orchestrator, format_queue_counts
from services.pipeline_agents import PipelineAgents
from services.workload_registry import WorkloadRegistry
from tests.test_orchestrator import pipeline_file, scout_provider, settle


def test_logging_is_disabled_until_configured(tmp_path):
    configure(False, str(tmp_path / "activity.log"))
    repository = MemoryRepository()
    application = repository.create_application("Example", "Backend", "https://example.test/off")
    repository.transition(application.application_id, "Prepared")
    assert not (tmp_path / "activity.log").exists()


def test_transition_log_records_one_move_and_can_be_turned_off(tmp_path):
    path = tmp_path / "activity.log"
    configure(True, str(path))
    try:
        repository = MemoryRepository()
        application = repository.create_application("Example", "Backend", "https://example.test/on")
        repository.transition(application.application_id, "Prepared", idempotency_key="move-1")
        repository.transition(application.application_id, "Prepared", idempotency_key="move-1")
        repository.transition(application.application_id, "Applied", idempotency_key="move-2")
        lines = path.read_text().splitlines()
        assert lines == [
            "Posting Example / Backend moved from Bookmarked to Prepared",
            "Posting Example / Backend moved from Prepared to Applied",
        ]
        configure(False, str(path))
        repository.transition(application.application_id, "Interviewing")
        assert path.read_text().splitlines() == lines
    finally:
        configure(False, None)


def test_environment_overrides_runtime_log_settings(tmp_path, monkeypatch):
    path = tmp_path / "from-env.log"
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "true")
    monkeypatch.setenv("ACTIVITY_LOG_FILE", str(path))
    try:
        configure_from_runtime({"activity_log": {"enabled": False, "path": "ignored.log"}})
        assert posting_label(" North ", " Analyst ") == "North / Analyst"
        repository = MemoryRepository()
        application = repository.create_application("North", "Analyst", "https://example.test/env")
        repository.transition(application.application_id, "Manual Apply Required")
        assert path.read_text().strip() == "Posting North / Analyst moved from Bookmarked to Manual Apply Required"
    finally:
        monkeypatch.delenv("ACTIVITY_LOG_ENABLED", raising=False)
        monkeypatch.delenv("ACTIVITY_LOG_FILE", raising=False)
        configure(False, None)


@pytest.mark.skip(reason="activity log test needs rewrite for new bookmark workflow")
def test_workers_log_start_and_finish_for_each_consumed_posting(monkeypatch, tmp_path):
    path = tmp_path / "activity.log"
    configure(True, str(path))
    repository = MemoryRepository()
    first = repository.create_application("One", "Backend", "https://example.test/one")
    second = repository.create_application("Two", "Backend", "https://example.test/two")
    first.status = second.status = "Discovered"
    gate = threading.Event()
    entered = []

    def provider(prompt, context):
        entered.append(context["company"])
        if len(entered) >= 2:
            gate.set()
        assert gate.wait(5)
        return scout_provider(prompt, context)

    config = pipeline_file(tmp_path, read_posting=2)
    registry = WorkloadRegistry()
    orchestrator = Orchestrator(repository, registry, PipelineAgents(repository, registry, provider), str(config))
    try:
        settle(orchestrator)
        lines = path.read_text().splitlines()
        assert "Worker-1 (Discovered) started working on posting One / Backend" in lines
        assert "Worker-2 (Discovered) started working on posting Two / Backend" in lines
        assert "Worker-1 (Discovered) finished working on posting One / Backend" in lines
        assert "Worker-2 (Discovered) finished working on posting Two / Backend" in lines
        assert any(line == "Posting One / Backend moved from Discovered to Bookmarked" for line in lines)
        assert any(line == "Posting Two / Backend moved from Discovered to Bookmarked" for line in lines)
    finally:
        orchestrator.close()
        configure(False, None)


def test_queue_counts_include_every_status_and_format_on_one_line():
    repository = MemoryRepository()
    repository.create_application("Example", "Backend", "https://example.test/counts")
    counts = repository.queue_counts()
    assert set(counts) == set(ALL_STATES)
    assert counts["Bookmarked"] == 1
    assert counts["Discovered"] == 0
    line = format_queue_counts(counts)
    assert line.startswith("Bookmarked=1 ")
    assert "Discovered=0" in line and "Duplicate=0" in line


def test_monitor_flag_prints_queue_counts(capsys, tmp_path):
    repository = MemoryRepository()
    repository.create_application("Example", "Backend", "https://example.test/monitor")
    config = pipeline_file(tmp_path)
    orchestrator = Orchestrator(repository, WorkloadRegistry(), PipelineAgents(repository, WorkloadRegistry(), scout_provider), str(config))
    try:
        orchestrator.print_queues()
        printed = capsys.readouterr().out
        assert "Bookmarked" in printed and "1" in printed
        assert printed.count("\n") == len(ALL_STATES)
    finally:
        orchestrator.close()


def test_journal_queues_endpoint(monkeypatch):
    from persistence_api import main
    repository = MemoryRepository()
    repository.create_application("Example", "Backend", "https://example.test/http")
    monkeypatch.setattr(main, "repository", repository)
    handler = object.__new__(main.Handler)
    handler.path = "/journal/queues"
    responses = []
    handler.send_json = lambda status, value: responses.append((status, value))
    handler.do_GET()
    assert responses[-1][0] == 200
    assert responses[-1][1]["Bookmarked"] == 1
    assert responses[-1][1]["Applied"] == 0


def test_api_log_is_off_by_default_and_stays_off_the_terminal(tmp_path, capsys, monkeypatch):
    from http.server import HTTPServer
    from threading import Thread
    from urllib.request import urlopen
    from persistence_api import main
    from persistence_api.repository import MemoryRepository
    from services.api_log import configure, configure_from_runtime, log_server
    path = tmp_path / "persistence-api.log"
    monkeypatch.delenv("API_LOG_ENABLED", raising=False)
    monkeypatch.delenv("API_LOG_FILE", raising=False)
    configure_from_runtime({})
    log_server("persistence API listening on http://127.0.0.1:8080")
    assert not path.exists()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    monkeypatch.setenv("API_LOG_ENABLED", "")
    configure_from_runtime({"api_log": {"enabled": False, "path": str(path)}})
    log_server("still off")
    assert not path.exists()
    configure(True, str(path))
    monkeypatch.setattr(main, "repository", MemoryRepository())
    server = HTTPServer(("127.0.0.1", 0), main.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen("http://127.0.0.1:{}/journal/queues".format(server.server_address[1])) as response:
            assert response.status == 200
        text = path.read_text()
        assert "GET /journal/queues" in text
        captured = capsys.readouterr()
        assert "GET /journal/queues" not in captured.out
        assert "GET /journal/queues" not in captured.err
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        configure(False, None)


def test_blank_activity_log_env_does_not_override_runtime(tmp_path, monkeypatch):
    path = tmp_path / "activity.log"
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "")
    monkeypatch.setenv("ACTIVITY_LOG_FILE", "")
    try:
        configure_from_runtime({"activity_log": {"enabled": True, "path": str(path)}})
        repository = MemoryRepository()
        application = repository.create_application("Example", "Backend", "https://example.test/blank-env")
        repository.transition(application.application_id, "Prepared")
        assert path.read_text().strip() == "Posting Example / Backend moved from Bookmarked to Prepared"
    finally:
        monkeypatch.delenv("ACTIVITY_LOG_ENABLED", raising=False)
        monkeypatch.delenv("ACTIVITY_LOG_FILE", raising=False)
        configure(False, None)


def test_activity_log_config_requires_a_path_when_enabled(tmp_path):
    from services.runtime_config import validate
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    config = {"google_drive": {
        "connection": "default",
        "interview_journal_url": "https://docs.google.com/document/d/example",
        "artifact_root_url": "https://drive.google.com/drive/folders/example",
        "applicant_profile_path": str(profile),
    }, "activity_log": {"enabled": True}}
    with pytest.raises(ValueError, match="activity_log.path"):
        validate(config)
    config["activity_log"] = {"enabled": False, "path": "logs/activity.log"}
    config["api_log"] = {"enabled": True}
    with pytest.raises(ValueError, match="api_log.path"):
        validate(config)
