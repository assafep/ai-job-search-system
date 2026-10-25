"""Optional activity lines for transitions and pipeline workers.

Disabled unless runtime configuration or the environment turns it on. Both the
Application Service and the orchestrator append to the same file.
"""

import fcntl
import os
import sys
from threading import Lock


_enabled = False
_path = None
_lock = Lock()


def configure(enabled, path=None):
    """Enable or disable the activity log. Enabling requires a file path."""
    global _enabled, _path
    if enabled and not (isinstance(path, str) and path.strip()):
        raise ValueError('activity log file is required when logging is enabled')
    _enabled = bool(enabled)
    _path = path.strip() if isinstance(path, str) else None


def configure_from_runtime(config=None):
    """Apply config/runtime.json, then let the environment override it."""
    section = (config or {}).get('activity_log') or {}
    if not isinstance(section, dict):
        section = {}
    enabled = bool(section.get('enabled', False))
    path = section.get('path')
    override = os.environ.get('ACTIVITY_LOG_ENABLED')
    if override is not None and override.strip():
        enabled = override.strip().lower() in {'1', 'true', 'yes', 'on'}
    if os.environ.get('ACTIVITY_LOG_FILE', '').strip():
        path = os.environ['ACTIVITY_LOG_FILE']
    configure(enabled, path)


def posting_label(company, role):
    return '{} / {}'.format((company or '').strip(), (role or '').strip()).strip(' /') or 'unknown'


def log_transition(company, role, source, target):
    if not source or source == target:
        return
    write('Posting {} moved from {} to {}'.format(posting_label(company, role), source, target))


def log_worker(worker, queue, company, role, phase):
    write('Worker-{} ({}) {} working on posting {}'.format(
        worker, queue, phase, posting_label(company, role)))


def write(line):
    if not _enabled or not _path:
        return
    try:
        directory = os.path.dirname(os.path.abspath(_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with _lock:
            with open(_path, 'a') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                handle.write(line + '\n')
                handle.flush()
    except OSError as error:
        sys.stderr.write('activity log failed: {}\n'.format(error))
