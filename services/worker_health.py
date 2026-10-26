"""Readiness for the current Linux worker process, including across restarts."""
import json
import os
from pathlib import Path


def process_identity(pid):
    # Linux field 22 is the process start time in clock ticks since boot.
    return Path('/proc/' + str(pid) + '/stat').read_text().rsplit(')', 1)[1].split()[19]


def mark_ready(path):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'pid': os.getpid(), 'started': process_identity(os.getpid())}))
    temporary.replace(path)


def healthy(path):
    try:
        record = json.loads(path.read_text())
        return process_identity(int(record['pid'])) == record['started']
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return False


if __name__ == '__main__':
    raise SystemExit(0 if healthy(Path(os.environ['AJSS_WORKER_READY_PATH'])) else 1)
