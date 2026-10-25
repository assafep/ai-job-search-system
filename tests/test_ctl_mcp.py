import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_codex_registration_runs_on_host_without_docker_or_tokens(tmp_path):
    codex = tmp_path / 'codex'
    codex.write_text('#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
    codex.chmod(0o755)
    result = subprocess.run([str(ROOT / 'deployment' / 'ctl.sh'), 'codex'], text=True,
        capture_output=True, env=dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH']))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['mcp', 'add', 'ajss-application-service', '--',
                                        str(ROOT / 'deployment' / 'ctl.sh'), 'mcp-stdio']


def test_stdio_build_logs_do_not_corrupt_protocol_stdout(tmp_path):
    docker = tmp_path / 'docker'
    docker.write_text('''#!/bin/sh
case "$1" in
  image) exit 1 ;;
  build) echo build-log ;;
  run)
    case " $* " in
      *" -i "*) ;;
      *) exit 3 ;;
    esac
    echo '{"jsonrpc":"2.0","id":1,"result":{}}'
    ;;
esac
''')
    docker.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               CLOUDSDK_CONFIG=str(tmp_path / 'gcloud'))
    env.pop('AJSS_INSIDE_PROVISION', None)
    result = subprocess.run([str(ROOT / 'deployment' / 'ctl.sh'), 'mcp-stdio'], text=True,
                            capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['jsonrpc'] == '2.0'
    assert 'build-log' in result.stderr
