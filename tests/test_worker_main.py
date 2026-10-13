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
