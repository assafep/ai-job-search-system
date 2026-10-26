import json

from services.worker_main import load_provider, main


def test_worker_provider_loader_accepts_module_attribute():
    assert load_provider("services.worker_main:load_provider") is load_provider


def test_worker_main_reports_missing_provider(capsys):
    assert main(["--once", "--provider", "missing.module:provider"]) == 1
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_worker_main_rejects_limit_without_one_shot(capsys):
    assert main(["--limit", "1", "--provider", "missing.module:provider"]) == 1
    assert "requires --once" in json.loads(capsys.readouterr().out)["error"]


def test_worker_marks_readiness_and_drains_on_sigterm(tmp_path, monkeypatch):
    import os
    import signal
    from types import SimpleNamespace
    from services import worker_main
    ready = tmp_path / 'ready'
    monkeypatch.setenv('AJSS_WORKER_READY_PATH', str(ready))
    monkeypatch.setenv('AJSS_RUNNER_LOCK_PATH', str(tmp_path / 'runner.lock'))
    monkeypatch.setattr(worker_main, 'load_provider', lambda *_: lambda *_: {})
    runtime = SimpleNamespace(registry=SimpleNamespace(snapshot=lambda: []))
    monkeypatch.setattr(worker_main, 'runtime_from_environment', lambda: runtime)
    previous = signal.getsignal(signal.SIGTERM)
    def run_forever(self, seconds, stop_event=None, poll_stages=False):
        assert json.loads(ready.read_text())['pid'] == os.getpid()
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert stop_event.is_set()
    monkeypatch.setattr(worker_main.WorkloadWorker, 'run_forever', run_forever)
    assert worker_main.main([]) == 0
    assert not ready.exists()
    assert signal.getsignal(signal.SIGTERM) == previous


def test_worker_refuses_interrupted_work_and_second_registry_writer(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from services import worker_main
    from services.orchestration_lock import runner_lock
    monkeypatch.setenv('AJSS_RUNNER_LOCK_PATH', str(tmp_path / 'runner.lock'))
    monkeypatch.setattr(worker_main, 'load_provider', lambda *_: lambda *_: {})
    runtime = SimpleNamespace(registry=SimpleNamespace(snapshot=lambda: [SimpleNamespace(status='running')]))
    monkeypatch.setattr(worker_main, 'runtime_from_environment', lambda: runtime)
    assert worker_main.main([]) == 1
    assert 'manual reconciliation' in capsys.readouterr().out
    with runner_lock():
        assert worker_main.main([]) == 1
    assert 'Another queue/orchestrator process' in capsys.readouterr().out


def test_readiness_rejects_a_stale_file_from_another_process_lifetime(tmp_path):
    import os
    from services.worker_health import healthy, mark_ready
    path = tmp_path / 'ready'
    assert not healthy(path)
    mark_ready(path)
    assert healthy(path)
    path.write_text(json.dumps({'pid': os.getpid(), 'started': 'old-process-start'}))
    assert not healthy(path)
