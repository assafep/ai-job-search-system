"""Assign an existing My Drive folder as the AJSS storage root.

Run this yourself. The only prompt is the Google browser login when the
current gcloud account lacks the Drive scope. A second run with the same
path reuses the project, service account, key, and writer grant.

    python3 -m provisioning.drive_folder --path 'My Drive/JobSearch/JobSearch2026'
"""
import argparse
import json
import sys
from pathlib import Path

from .drive_login import ensure_file_login
from .drive_storage import bind
from .state import ProvisioningFailure

RUNTIME = Path(__file__).resolve().parents[1] / 'config' / 'runtime.json'


def assign_runtime_root(folder_id, runtime_path=RUNTIME):
    config = json.loads(runtime_path.read_text())
    url = 'https://drive.google.com/drive/folders/' + folder_id
    drive = config.setdefault('google_drive', {})
    changed = drive.get('artifact_root_url') != url
    drive['artifact_root_url'] = url
    temporary = runtime_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(config, indent=2) + '\n')
    temporary.replace(runtime_path)
    return url, changed


def provision(path):
    """Bind the folder and point the AJSS runtime artifact root at it."""
    result = bind(path)
    url, root_changed = assign_runtime_root(result['folder_id'])
    file_login_created = ensure_file_login(result['project_id'])
    reported = dict(result)
    reported['artifact_root_url'] = url
    reported['root_changed'] = root_changed
    reported['file_login_created'] = file_login_created
    if file_login_created or root_changed:
        reported['outcome'] = 'changed'
    return reported


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', required=True, help='Existing folder path, for example My Drive/JobSearch/JobSearch2026')
    args = parser.parse_args(argv)
    print('1. Use the logged-in gcloud account, or open a browser if Drive access is missing.')
    print('2. Resolve the existing folder. The path is not created.')
    print('3. Ensure one Cloud project, drive-writer service account, and one local key.')
    print('4. Grant that account writer on the folder and probe a create-then-trash.')
    print('5. Set config/runtime.json google_drive.artifact_root_url to that folder.')
    print('6. Save a drive.file user login if one is not already stored.')
    try:
        result = provision(args.path)
        url, root_changed = result['artifact_root_url'], result['root_changed']
    except (ProvisioningFailure, OSError, ValueError) as exc:
        print('Drive setup stopped: ' + str(exc), file=sys.stderr)
        return 1
    print('Root directory: ' + args.path)
    print('Drive folder {}: {}'.format(result['outcome'], result['folder_id']))
    print('Project: ' + result['project_id'])
    print('Service account: ' + result['service_account'])
    print('Write probe trashed file: ' + result['write_probe_trashed'])
    print('AJSS artifact root {}: {}'.format('changed' if root_changed else 'unchanged', url))
    print('drive.file login: ' + ('saved' if result['file_login_created'] else 'already present'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
