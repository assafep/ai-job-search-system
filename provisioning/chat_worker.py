"""Install/start the workstation's stage task worker, using saved API auth.

No application queue is run by installation. Only explicit stage task requests
are consumed. This host must remain online for stage execution.
"""
import os
from pathlib import Path
import shlex
import subprocess
import sys

from services.runtime_config import ROOT, load


def install():
    load(require=True)
    root = Path(ROOT)
    credentials = root / '.onboarding' / 'application-service.env'
    if not credentials.is_file():
        raise RuntimeError('Configure .onboarding/application-service.env first')
    for name in ('workloads.json', 'approvals.json'):
        path = root / 'config' / name
        if path.exists() and not os.access(path, os.R_OK):
            raise RuntimeError('Worker cannot read ' + name)
    # Preserve the explicitly configured scout executable from the normal host env.
    agent = os.environ.get('CODING_AGENT', '')
    source = root / '.env'
    if not agent and source.exists():
        for line in source.read_text().splitlines():
            line = line.removeprefix('export ').strip()
            if line.startswith('CODING_AGENT='):
                values = shlex.split(line.split('=', 1)[1])
                agent = values[0] if values else ''
    agent = agent or (load(require=True).get('scout') or {}).get('coding_agent') or 'codex'
    template = (root / 'deployment/systemd/ajss-stage-task-worker.service.in').read_text()
    values = {'REPO': str(root), 'PYTHON': sys.executable, 'PATH': os.environ['PATH'], 'CODING_AGENT': agent}
    for key, value in values.items():
        if any(c in value for c in '\n\r"%'):
            raise ValueError('Unsupported service setting')
        template = template.replace('@' + key + '@', value)
    directory = Path.home() / '.config/systemd/user'
    directory.mkdir(parents=True, exist_ok=True)
    unit = directory / 'ajss-stage-task-worker.service'
    unit.write_text(template)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', unit.name], check=True)
    print('Installed and started ' + unit.name)
    print('Worker host must remain online; no application queue was requested.')


if __name__ == '__main__':
    install()
