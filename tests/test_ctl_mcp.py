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
    assert json.loads(result.stdout.splitlines()[0]) == ['mcp', 'add', 'ajss-application-service', '--',
                                        str(ROOT / 'deployment' / 'ctl.sh'), 'mcp-stdio']


def test_stdio_build_logs_do_not_corrupt_protocol_stdout(tmp_path):
    docker = tmp_path / 'docker'
    docker.write_text('''#!/bin/sh
case "$1" in
  image) exit 1 ;;
  build) echo build-log ;;
  run)
    case " $* " in
      *" --user $(id -u):$(id -g) "*) ;;
      *) exit 4 ;;
    esac
    case " $* " in
      *" --tmpfs /home/ajss:rw,mode=700,uid=$(id -u),gid=$(id -g) "*) ;;
      *) exit 5 ;;
    esac
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


def test_up_injects_host_grok_and_logins_without_mounting_parent_checkout(tmp_path):
    docker = tmp_path / 'docker'
    docker.write_text('''#!/usr/bin/env python3
import json, sys
if sys.argv[1] == 'image':
    sys.exit(1)
if sys.argv[1] == 'run':
    print(json.dumps(sys.argv[2:]))
''')
    docker.chmod(0o755)
    for command in ('grok', 'codex'):
        executable = tmp_path / command
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o755)
    home = tmp_path / 'home with spaces'
    for directory in (home / '.grok', home / '.codex'):
        directory.mkdir(parents=True)
        (directory / 'auth.json').write_text('{}')
    endpoint = tmp_path / 'docker.sock'
    endpoint.touch()
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               HOME=str(home), CODEX_HOME=str(home / '.codex'),
               CLOUDSDK_CONFIG=str(tmp_path / 'gcloud'), DOCKER_HOST='unix://' + str(endpoint))
    env.pop('AJSS_INSIDE_PROVISION', None)
    result = subprocess.run([str(ROOT / 'deployment/ctl.sh'), 'up', '--path', 'My Drive/with spaces'],
                            text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert str(tmp_path / 'grok') + ':/opt/ajss-host-cli/grok:ro' in args
    assert str(home / '.grok') + ':/home/ajss/.grok' in args
    assert str(home / '.codex') + ':/home/ajss/.codex' in args
    assert str(endpoint) + ':/run/ajss-docker.sock' in args
    assert str(ROOT) + ':' + str(ROOT) in args
    assert str(ROOT / '..') + ':' + str(ROOT / '..') not in args
    assert args[-3:] == ['up', '--path', 'My Drive/with spaces']


def test_consent_request_opens_host_browser_and_cleans_relay(tmp_path):
    import sys
    docker = tmp_path / 'docker'
    docker.write_text('#!' + sys.executable + '\n' + '''
import json, pathlib, sys, time
if sys.argv[1] == 'image':
    sys.exit(1)
if sys.argv[1] == 'run':
    relay = pathlib.Path(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('AJSS_BROWSER_RELAY_DIR=')))
    (relay / 'request.tmp').write_text('https://accounts.google.com/o/oauth2/v2/auth?state=test&scope=test\\n')
    (relay / 'request.tmp').replace(relay / 'request.url')
    deadline = time.monotonic() + 5
    while not (relay / 'result').exists() and time.monotonic() < deadline:
        time.sleep(.05)
    print(json.dumps({'relay': str(relay), 'opened': (relay / 'result').read_text().strip()}))
''')
    docker.chmod(0o755)
    opener = tmp_path / 'xdg-open'
    opener.write_text('#!/bin/sh\nprintf "%s" "$1" > "$BROWSER_LOG"\n')
    opener.chmod(0o755)
    log = tmp_path / 'browser.log'
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               BROWSER_LOG=str(log), CLOUDSDK_CONFIG=str(tmp_path / 'gcloud'))
    env.pop('AJSS_INSIDE_PROVISION', None)
    result = subprocess.run([str(ROOT / 'deployment/ctl.sh'), 'drive-login'],
                            text=True, capture_output=True, env=env, timeout=10)
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response['opened'] == '1'
    assert log.read_text() == 'https://accounts.google.com/o/oauth2/v2/auth?state=test&scope=test'
    assert not Path(response['relay']).exists()


def test_runner_control_commands_receive_engine_socket(tmp_path):
    docker = tmp_path / 'docker'
    docker.write_text('''#!/usr/bin/env python3
import json, sys
if sys.argv[1] == 'image':
    sys.exit(1)
if sys.argv[1] == 'run':
    print(json.dumps(sys.argv[2:]))
''')
    docker.chmod(0o755)
    endpoint = tmp_path / 'docker.sock'
    endpoint.touch()
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
               CLOUDSDK_CONFIG=str(tmp_path / 'gcloud'), DOCKER_HOST='unix://' + str(endpoint))
    env.pop('AJSS_INSIDE_PROVISION', None)
    for command in ('start', 'stop', 'status'):
        result = subprocess.run([str(ROOT / 'deployment/ctl.sh'), command, '--timeout', '3'],
                                text=True, capture_output=True, env=env, timeout=10)
        assert result.returncode == 0, result.stderr
        args = json.loads(result.stdout)
        assert str(endpoint) + ':/run/ajss-docker.sock' in args
        assert args[-3:] == [command, '--timeout', '3']
