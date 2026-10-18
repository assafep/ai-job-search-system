"""Redraw queue counts in place by polling the persistence API."""

import argparse
import sys
from time import sleep

from persistence_api.journal_format import ALL_STATES


def queue_lines(counts, error=None):
    """Fixed-height lines so a refresh overwrites the same screen rows."""
    if error:
        lines = ['unavailable: {}'.format(error)]
        lines.extend('' for _ in range(len(ALL_STATES) - 1))
        return lines
    counts = counts or {}
    return ['{:<32}{:>4}'.format(state, int(counts.get(state, 0))) for state in ALL_STATES]


def paint_queues(counts, error=None):
    """Redraw from the top of the screen and erase anything left below the frame."""
    lines = queue_lines(counts, error)
    chunks = ['\033[H']
    for line in lines:
        chunks.append('\033[2K{}\n'.format(line))
    chunks.append('\033[J')
    return ''.join(chunks)


def watch(fetch, output=None, interval=1, iterations=None, sleeper=sleep):
    """Poll until interrupted. The frame stays on the same lines."""
    output = output or sys.stdout
    output.write('\033[?1049h\033[?25l')
    shown = 0
    try:
        while iterations is None or shown < iterations:
            try:
                frame = paint_queues(fetch())
            except Exception as error:
                frame = paint_queues({}, error=str(error))
            output.write(frame)
            output.flush()
            shown += 1
            if iterations is not None and shown >= iterations:
                break
            sleeper(interval)
    finally:
        output.write('\033[?25h\033[?1049l')
        output.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval', type=float, default=1, help='Seconds between refreshes')
    args = parser.parse_args(argv)
    if args.interval <= 0:
        raise ValueError('interval must be positive')
    from .persistence_client import PersistenceApiClient
    client = PersistenceApiClient()
    try:
        watch(client.queue_counts, interval=args.interval)
    except KeyboardInterrupt:
        sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
