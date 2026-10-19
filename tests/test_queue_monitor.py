import io

import pytest

from persistence_api.journal_format import ALL_STATES
from services.orchestrator import Orchestrator, parse_worker_overrides
from services.pipeline_agents import PipelineAgents
from services.queue_monitor import paint_queues, queue_lines, watch
from services.workload_registry import WorkloadRegistry
from tests.test_orchestrator import pipeline_file, scout_provider


def test_queue_frame_rewrites_the_same_lines():
    first = paint_queues({'Bookmarked': 1, 'Discovered': 4})
    second = paint_queues({'Bookmarked': 2, 'Discovered': 0})
    assert first.startswith('\033[H')
    assert first.endswith('\033[J')
    assert '\033[2K' in first
    assert first.count('\n') == len(ALL_STATES)
    assert 'Bookmarked' in first and '   1' in first
    assert second.startswith('\033[H') and second.endswith('\033[J')
    assert second.count('\n') == len(ALL_STATES)
    assert '   2' in second
    assert len(queue_lines({}, error='down')) == len(ALL_STATES)


def test_watch_updates_values_without_adding_a_second_frame():
    frames = [{'Bookmarked': 1}, {'Bookmarked': 9}]
    output = io.StringIO()
    watch(lambda: frames.pop(0), output=output, iterations=2, sleeper=lambda _seconds: None)
    text = output.getvalue()
    assert text.count('\n') == len(ALL_STATES) * 2
    assert text.count('\033[H') == 2
    assert text.count('\033[J') == 2
    assert '   1' in text and '   9' in text
    assert text.startswith('\033[?1049h\033[?25l')
    assert text.endswith('\033[?25h\033[?1049l')


def test_worker_flags_override_the_config_file_and_survive_reload(tmp_path):
    path = pipeline_file(tmp_path, bookmark=1)
    assert parse_worker_overrides(['bookmark=2', 'auto-apply=0']) == {'bookmark': 2, 'auto-apply': 0}
    try:
        parse_worker_overrides(['scout'])
    except ValueError as error:
        assert 'pipeline=count' in str(error)
    else:
        raise AssertionError('a bare pipeline name must be rejected')
    try:
        parse_worker_overrides(['bookmark=65'])
    except ValueError as error:
        assert 'between 0 and 64' in str(error)
    else:
        raise AssertionError('an out-of-range count must be rejected')

    registry = WorkloadRegistry()
    orchestrator = Orchestrator(
        object(), registry, PipelineAgents(object(), registry, scout_provider), str(path),
        worker_overrides={'bookmark': 3, 'prepare': 2})
    try:
        assert orchestrator.config['pipelines']['bookmark']['workers'] == 3
        assert orchestrator.config['pipelines']['prepare']['workers'] == 2
        assert orchestrator.config['pipelines']['submit']['workers'] == 0
        saved = path.read_text().replace('"workers": 1', '"workers": 9', 1)
        path.write_text(saved)
        orchestrator.reload()
        assert orchestrator.config['pipelines']['bookmark']['workers'] == 3
        assert orchestrator.config['pipelines']['prepare']['workers'] == 2
        try:
            Orchestrator(object(), WorkloadRegistry(), PipelineAgents(object(), WorkloadRegistry(), scout_provider),
                         str(path), worker_overrides={'missing': 1})
        except ValueError as error:
            assert 'unknown pipeline' in str(error)
        else:
            raise AssertionError('an unknown pipeline override must be rejected')
    finally:
        orchestrator.close()


def test_run_script_accepts_monitor_log_and_worker_flags(tmp_path, monkeypatch):
    from services import activity_log
    from services.run import apply_log_flags, parse_args
    with pytest.raises(SystemExit):
        parse_args(['--log', '--no-log'])
    args = parse_args(['--monitor', '--workers', 'bookmark=2', '--workers', 'preparation=0'])
    assert args.monitor is True
    assert args.workers == ['bookmark=2', 'preparation=0']
    monkeypatch.setenv('ACTIVITY_LOG_ENABLED', '1')
    monkeypatch.setenv('ACTIVITY_LOG_FILE', str(tmp_path / 'env.log'))
    runtime = {'activity_log': {'enabled': True, 'path': str(tmp_path / 'runtime.log')}}
    try:
        apply_log_flags(parse_args(['--no-log']), runtime)
        activity_log.log_transition('A', 'B', 'Discovered', 'Bookmarked')
        assert not (tmp_path / 'env.log').exists()
        path = tmp_path / 'activity.log'
        apply_log_flags(parse_args(['--log-file', str(path)]), runtime)
        activity_log.log_transition('A', 'B', 'Discovered', 'Bookmarked')
        assert path.read_text() == 'Posting A / B moved from Discovered to Bookmarked\n'
    finally:
        activity_log.configure(False, None)


def test_queue_requests_do_not_write_an_access_log(capsys, monkeypatch):
    from http.server import HTTPServer
    from threading import Thread
    from urllib.request import urlopen
    from persistence_api import main
    from persistence_api.repository import MemoryRepository
    monkeypatch.setattr(main, 'repository', MemoryRepository())
    server = HTTPServer(('127.0.0.1', 0), main.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen('http://127.0.0.1:{}/journal/queues'.format(server.server_address[1])) as response:
            assert response.status == 200
        captured = capsys.readouterr()
        assert 'GET /journal/queues' not in captured.err
        assert 'GET /journal/queues' not in captured.out
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
