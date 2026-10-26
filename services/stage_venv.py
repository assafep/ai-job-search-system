"""One virtualenv per stage that ships a requirements file.

The file lives at ``stages/<stage name>/requirements.txt``. Its SHA-256
is stored beside the virtualenv. A different hash deletes that virtualenv and
installs the file again from scratch.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENV_ROOT = os.path.join(ROOT, ".onboarding", "stage-venvs")


def requirements_path(stage_name):
    return os.path.join(ROOT, "stages", stage_name, "requirements.txt")


def _hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_create(venv_dir, requirements):
    python = os.path.join(venv_dir, "bin", "python")
    subprocess.check_call([sys.executable, "-m", "venv", venv_dir])
    subprocess.check_call([python, "-m", "pip", "install", "-r", requirements])


def ensure_stage_python(stage_name, create_venv=None):
    """Return the stage virtualenv's Python, or None when it has no requirements."""
    requirements = requirements_path(stage_name)
    if not os.path.isfile(requirements):
        return None
    digest = _hash_file(requirements)
    home = os.path.join(VENV_ROOT, stage_name)
    venv_dir = os.path.join(home, "venv")
    hash_path = os.path.join(home, "requirements.sha256")
    python = os.path.join(venv_dir, "bin", "python")
    stored = ""
    if os.path.isfile(hash_path):
        with open(hash_path) as handle:
            stored = handle.read().strip()
    if stored != digest or not os.path.isfile(python):
        if os.path.isdir(venv_dir):
            shutil.rmtree(venv_dir)
        os.makedirs(home, exist_ok=True)
        (create_venv or _default_create)(venv_dir, requirements)
        with open(hash_path, "w") as handle:
            handle.write(digest + "\n")
    if not os.path.isfile(python):
        raise RuntimeError("stage virtualenv did not produce " + python)
    return python


def main():
    """Run one already-bootstrapped stage. The parent process is the installer."""
    os.environ["STAGE_VENV_DELEGATED"] = "1"
    request = json.load(sys.stdin)
    from .application_service_client import ApplicationServiceClient
    from .stage_runner import run_one
    from .stage_executor import runtime_from_environment
    from .workload_registry import registry_from_environment
    client = ApplicationServiceClient()
    registry = registry_from_environment()
    runtime = runtime_from_environment(persistence_client=client)
    runtime.registry = registry
    result = run_one(
        client, registry, runtime, request["application"],
        stage_name=request.get("stage_name"), stage_task_id=request.get("stage_task_id"))
    json.dump(result, sys.stdout, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
