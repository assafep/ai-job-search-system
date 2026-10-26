"""One registry-writing runner per installation, shared with legacy queue runs."""

import fcntl
import os
from contextlib import contextmanager
from functools import wraps
from .runtime_config import ROOT


@contextmanager
def runner_lock():
    path = os.environ.get('AJSS_RUNNER_LOCK_PATH') or os.path.join(ROOT, 'config', 'concurrent-queues.lock')
    with open(path, 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another queue/orchestrator process is active; adjust its pipeline workers instead')
        yield


def exclusive_runner(function):
    @wraps(function)
    def run(*args, **kwargs):
        with runner_lock():
            return function(*args, **kwargs)
    return run
