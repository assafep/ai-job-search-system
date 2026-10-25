import os

from services import workflow_venv


def test_missing_requirements_uses_the_current_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow_venv, "ROOT", str(tmp_path))
    monkeypatch.setattr(workflow_venv, "VENV_ROOT", str(tmp_path / "venvs"))
    assert workflow_venv.ensure_workflow_python("bookmark-discovered") is None


def test_changed_requirements_rebuild_the_virtualenv(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow_venv, "ROOT", str(tmp_path))
    monkeypatch.setattr(workflow_venv, "VENV_ROOT", str(tmp_path / "venvs"))
    requirements = tmp_path / "workflows" / "prepare-validated" / "requirements.txt"
    requirements.parent.mkdir(parents=True)
    requirements.write_text("reportlab==5.1.0\n")
    created = []

    def create(venv_dir, path):
        created.append((venv_dir, open(path).read()))
        os.makedirs(os.path.join(venv_dir, "bin"))
        open(os.path.join(venv_dir, "bin", "python"), "w").close()

    first = workflow_venv.ensure_workflow_python("prepare-validated", create_venv=create)
    second = workflow_venv.ensure_workflow_python("prepare-validated", create_venv=create)
    requirements.write_text("reportlab==5.1.0\npypdf==3.17.4\n")
    third = workflow_venv.ensure_workflow_python("prepare-validated", create_venv=create)

    assert first == second
    assert os.path.isfile(first)
    assert third == first
    assert len(created) == 2
    assert "pypdf==3.17.4" in created[1][1]
    stored = (tmp_path / "venvs" / "prepare-validated" / "requirements.sha256").read_text().strip()
    assert len(stored) == 64
