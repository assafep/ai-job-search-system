"""Start the persistence API and the pipeline orchestrator in one process."""

import argparse
import errno
import os
import sys
from threading import Thread
from time import monotonic, sleep

from persistence_api.main import Handler, configure_activity_log, configure_api_log
from services.activity_log import configure, configure_from_runtime
from services.orchestrator import parse_worker_overrides, run_pipelines
from services.runtime_config import load as load_runtime


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--config', default=None, help='Pipeline config file. Defaults to config/pipelines.json')
    parser.add_argument('--monitor', action='store_true', help='Redraw queue counts on the same lines every second')
    parser.add_argument('--log', action='store_true', help='Write the activity log')
    parser.add_argument('--no-log', action='store_true', help='Do not write the activity log')
    parser.add_argument('--log-file', help='Activity log path. Implies --log')
    parser.add_argument('--workers', action='append', default=[], metavar='PIPELINE=COUNT',
                        help='Override one pipeline worker count. Repeat for each pipeline.')
    args = parser.parse_args(argv)
    if args.log and args.no_log:
        parser.error('use only one of --log and --no-log')
    if args.port <= 0 or args.port > 65535:
        parser.error('port must be between 1 and 65535')
    return args


def apply_log_flags(args, runtime):
    """Flags override config/runtime.json and the environment."""
    configure_from_runtime(runtime)
    if args.no_log:
        configure(False, None)
        return
    if not args.log and not args.log_file:
        return
    section = (runtime or {}).get('activity_log') or {}
    path = args.log_file or os.environ.get('ACTIVITY_LOG_FILE') or section.get('path')
    configure(True, path)


def start_api(host, port):
    """Listen in the background. Return None when this port is already serving."""
    from http.server import HTTPServer
    try:
        server = HTTPServer((host, port), Handler)
    except OSError as error:
        if error.errno in {errno.EADDRINUSE, errno.EACCES}:
            return None
        raise
    thread = Thread(target=server.serve_forever, name='persistence-api', daemon=True)
    thread.start()
    return server


def wait_for_api(client, timeout=15):
    deadline = monotonic() + timeout
    last_error = None
    while monotonic() < deadline:
        try:
            client.queue_counts()
            return
        except Exception as error:
            last_error = error
            sleep(0.05)
    raise RuntimeError('persistence API did not accept queue reads: {}'.format(last_error))


def main(argv=None):
    args = parse_args(argv)
    runtime = load_runtime(require=True)
    apply_log_flags(args, runtime)
    configure_activity_log()
    # apply_log_flags is authoritative; the API helper reads the same runtime
    # and would otherwise turn the flags back off.
    apply_log_flags(args, runtime)
    configure_api_log()
    os.environ['PERSISTENCE_API_URL'] = 'http://{}:{}'.format(args.host, args.port)
    server = start_api(args.host, args.port)
    from services.persistence_client import PersistenceApiClient
    client = PersistenceApiClient()
    wait_for_api(client)
    if server is None:
        sys.stderr.write(
            'persistence API is already listening on {}:{}; it keeps the log settings it loaded at startup until restarted\n'.format(
                args.host, args.port))
    overrides = parse_worker_overrides(args.workers)
    try:
        run_pipelines(args.config, None, overrides, args.monitor)
    except KeyboardInterrupt:
        if not args.monitor:
            sys.stdout.write('\n')
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
