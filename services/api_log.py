"""Optional request log for the Application Service.

Disabled unless runtime configuration or the environment turns it on. Nothing
from this log is written to the terminal.
"""

import fcntl
import os
import sys
from threading import Lock


_enabled = False
_path = None
_lock = Lock()


def configure(enabled, path=None):
    """Enable or disable the API log. Enabling requires a file path."""
    global _enabled, _path
    if enabled and not (isinstance(path, str) and path.strip()):
        raise ValueError('api log file is required when logging is enabled')
    _enabled = bool(enabled)
    _path = path.strip() if isinstance(path, str) else None


def configure_from_runtime(config=None):
    """Apply config/runtime.json, then let the environment override it."""
    section = (config or {}).get('api_log') or {}
    if not isinstance(section, dict):
        section = {}
    enabled = bool(section.get('enabled', False))
    path = section.get('path')
    override = os.environ.get('API_LOG_ENABLED')
    if override is not None and override.strip():
        enabled = override.strip().lower() in {'1', 'true', 'yes', 'on'}
    if os.environ.get('API_LOG_FILE', '').strip():
        path = os.environ['API_LOG_FILE']
    configure(enabled, path)


def log_server(message):
    write(message)


def log_access(address, when, message):
    write('{} - - [{}] {}'.format(address, when, message))


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
        sys.stderr.write('api log failed: {}\n'.format(error))
