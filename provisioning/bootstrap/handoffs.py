"""Atomic, private provisioning-file persistence."""
import json
import os
import tempfile

def save(path, value):
    """Atomic replacement, private even if the caller's umask is permissive."""
    fd, name = tempfile.mkstemp(prefix='.handoff-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
