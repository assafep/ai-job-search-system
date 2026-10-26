"""Atomic, private provisioning-file persistence."""
import json
import os
import tempfile

def save(path, value):
    """Atomic replacement, private even if the caller's umask is permissive."""
    import sys
    print(f'Saving handoff to {path}...', file=sys.stderr, flush=True)
    try:
        fd, name = tempfile.mkstemp(prefix='.handoff-', dir=path.parent)
    except OSError as exc:
        print(f'Failed to create temp file in {path.parent}: {exc}', file=sys.stderr, flush=True)
        raise
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        print(f'Saved handoff to {path}', file=sys.stderr, flush=True)
    except OSError as exc:
        print(f'Failed to write handoff {path}: {exc}', file=sys.stderr, flush=True)
        if os.path.exists(name):
            os.unlink(name)
        raise
    finally:
        if os.path.exists(name):
            os.unlink(name)
