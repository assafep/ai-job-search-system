"""Keep one onboard process for the whole local-run checklist.

The host talks to this process through var/onboard.sock. The Google token, the
Drive connection, and files already read or written stay in memory for the
whole wizard. A single stage uses that same process and those same resources.
The next step does not start a container or download those files again. The
first full report lists values that are already stored. When every required
value is stored, the same process runs the setup checks before asking whether
to continue.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

SOCKET_NAME = "onboard.sock"
MESSAGE_LIMIT = 2_000_000


def project_root():
    return Path(__file__).resolve().parents[2]


def socket_path(root):
    return Path(root) / "var" / SOCKET_NAME


def _read_line(conn):
    data = bytearray()
    while b"\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MESSAGE_LIMIT:
            raise ValueError("onboard message is too large")
    line, _, _ = data.partition(b"\n")
    return line.decode("utf-8")


def transact(root, payload, timeout=120):
    """Send one checklist request and return {"code", "output"} or {"pong": true}."""
    path = socket_path(root)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(path))
        client.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        return json.loads(_read_line(client))


def _ping(root):
    try:
        return transact(root, {"ping": True}, timeout=2).get("pong") is True
    except (OSError, json.JSONDecodeError, ValueError):
        return False


def _wait_ping(root, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _ping(root):
            return True
        time.sleep(0.05)
    return False


def _compose(root):
    command = os.environ.get("COMPOSE", "docker compose").split()
    return command + ["--profile", "dev"]


def ensure_session(root):
    """Start the onboard container when the socket is not answering."""
    root = Path(root)
    env_file = root / ".env"
    if not env_file.exists():
        env_file.touch()
        os.chmod(env_file, 0o600)
    if _ping(root):
        return
    subprocess.run(
        [*_compose(root), "up", "-d", "ajss-onboard"],
        cwd=root, check=True, stdout=sys.stderr,
    )
    if _wait_ping(root, 60):
        return
    subprocess.run(
        [*_compose(root), "restart", "ajss-onboard"],
        cwd=root, check=True, stdout=sys.stderr,
    )
    if not _wait_ping(root, 30):
        raise RuntimeError("The onboard session did not start")


def parse_call(argv):
    payload = {"set": [], "listen": False, "check": False, "stage": ""}
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--listen":
            payload["listen"] = True
        elif item == "--check":
            payload["check"] = True
        elif item == "--stage":
            index += 1
            if index >= len(argv):
                raise ValueError("--stage expects a step name")
            payload["stage"] = argv[index]
        elif item.startswith("--stage="):
            payload["stage"] = item.split("=", 1)[1]
        elif item == "--set":
            index += 1
            if index >= len(argv):
                raise ValueError("--set expects KEY=VALUE")
            payload["set"].append(argv[index])
        elif item.startswith("--set="):
            payload["set"].append(item.split("=", 1)[1])
        else:
            raise ValueError("unsupported onboard argument")
        index += 1
    return payload


def call_main(argv):
    root = project_root()
    payload = parse_call(argv)
    ensure_session(root)
    try:
        result = transact(root, payload)
    except (ConnectionError, FileNotFoundError, json.JSONDecodeError):
        if not _wait_ping(root, 10):
            raise RuntimeError("The onboard session restarted and did not come back")
        result = transact(root, payload)
    sys.stdout.write(result.get("output") or "")
    return int(result.get("code") or 0)


def _source_mtime(root):
    latest = 0.0
    for folder in ("ajss", "adapters"):
        base = Path(root) / folder
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            latest = max(latest, path.stat().st_mtime)
    return latest


def _dispatch(root, payload, resources):
    if payload.get("ping"):
        return {"pong": True}
    from argparse import Namespace
    from io import StringIO
    from contextlib import redirect_stdout
    from ajss.onboard.checklist import run_onboard
    args = Namespace(
        root=str(root),
        set=list(payload.get("set") or []),
        listen=bool(payload.get("listen")),
        check=bool(payload.get("check")),
        stage=str(payload.get("stage") or ""),
    )
    buffer = StringIO()
    try:
        with redirect_stdout(buffer):
            code = run_onboard(args, resources=resources)
    except Exception as exc:
        return {"code": 1, "output": type(exc).__name__ + ": " + str(exc) + "\n"}
    return {"code": int(code or 0), "output": buffer.getvalue()}


def _reexec(server, path):
    server.close()
    path.unlink(missing_ok=True)
    os.execv(sys.executable, [sys.executable, "-m", "ajss.cli", "onboard-serve"])


def serve(root=None, reload=True):
    """Answer checklist requests until the process is stopped."""
    import signal
    root = Path(root or project_root())
    path = socket_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chmod(path, 0o666)
    server.listen(4)
    server.settimeout(1.0)
    started = _source_mtime(root)
    resources = {}
    stopping = False

    def _stop(signum, frame):
        nonlocal stopping
        stopping = True

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    try:
        while not stopping:
            if reload and _source_mtime(root) > started:
                _reexec(server, path)
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            except InterruptedError:
                continue
            try:
                if reload and _source_mtime(root) > started:
                    conn.close()
                    _reexec(server, path)
                payload = json.loads(_read_line(conn) or "{}")
                result = _dispatch(root, payload, resources)
                conn.sendall(json.dumps(result).encode("utf-8") + b"\n")
            except Exception as exc:
                try:
                    message = {"code": 1, "output": type(exc).__name__ + ": " + str(exc) + "\n"}
                    conn.sendall(json.dumps(message).encode("utf-8") + b"\n")
                except OSError:
                    pass
            finally:
                conn.close()
    finally:
        server.close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        sys.exit(call_main(sys.argv[1:]))
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        sys.stderr.write(str(exc) + "\n")
        sys.exit(1)
