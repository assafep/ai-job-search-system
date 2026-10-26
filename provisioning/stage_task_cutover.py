"""One-time offline conversion of operational JSON files during a stopped rollout.

These source names are migration inputs, never accepted by runtime loaders.
"""
import argparse
import json
import os
from pathlib import Path

RENAMES = {'workflow': 'stage', 'workflows': 'stages', 'run_id': 'stage_task_id'}


def transform(value):
    if isinstance(value, list):
        return [transform(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        name, item = RENAMES.get(key, key), transform(item)
        if name in result and result[name] != item:
            raise ValueError('Conflicting old and new identity fields')
        result[name] = item
    return result


def convert(path):
    """Validate before writing; preserve a private backup and atomically replace."""
    path = Path(path)
    before = path.read_bytes()
    after = transform(json.loads(before))
    if after == json.loads(before):
        return False
    backup = path.with_name(path.name + '.before-stage-cutover')
    if backup.exists() and backup.read_bytes() != before:
        raise ValueError('A different cutover backup already exists: ' + str(backup))
    if not backup.exists():
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(before)
    temporary = path.with_name(path.name + '.stage-cutover.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(after, handle, indent=2)
            handle.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', help='Explicit operational registry/config JSON paths; workers must be stopped')
    args = parser.parse_args(argv)
    for path in args.files:
        print(path + ': ' + ('converted' if convert(path) else 'unchanged'))


if __name__ == '__main__':
    main()
