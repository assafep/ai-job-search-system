"""Local Chrome submission adapter using visible Comeet form controls."""

import json
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def submit(context):
    path = os.environ.get('SUBMISSION_DESTINATIONS_PATH') or os.path.join(ROOT, 'config', 'submission-destinations.json')
    with open(path) as handle:
        destinations = json.load(handle)
    application = context['application']
    destination = destinations.get(application['application_id'])
    if not destination or any(destination[key].lower() != application[key].lower() for key in ('company', 'role')):
        return {'status': 'Blocked', 'blockers': ['No verified employer application destination is configured for this posting.']}
    node = shutil.which('node')
    chrome = os.environ.get('BROWSER_EXECUTABLE') or shutil.which('google-chrome') or shutil.which('chromium')
    if not node or not chrome:
        return {'status': 'Blocked', 'failure_kind': 'system_error', 'blockers': ['Local Node.js and Chrome are required by the browser submission adapter.']}
    with tempfile.TemporaryDirectory(prefix='job-application-') as directory:
        name = os.path.basename(context['package']['name'])
        package_path = os.path.join(directory, name)
        with open(package_path, 'wb') as handle:
            handle.write(context['package_bytes'])
        payload = {
            'application': application,
            'destination': destination,
            'profile': context['applicant_profile'],
            'package_path': package_path,
            'package_name': name,
            'chrome': chrome,
        }
        process = subprocess.run([node, os.path.join(ROOT, 'adapters', 'browser', 'comeet.cjs')],
                                 input=json.dumps(payload), capture_output=True, text=True, timeout=240)
        if process.returncode:
            # Do not print browser messages, which can contain iframe tokens.
            raise RuntimeError('browser adapter interrupted; inspect the recorded unconfirmed attempt before retrying')
        return json.loads(process.stdout)
