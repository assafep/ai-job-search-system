"""Process eligible applications from a specific queue with real side effects.

Usage examples:
  # Process all eligible items from the Discovered queue:
  python3 -m services.run_single Discovered

  # Process a specific application from the Bookmarked queue:
  python3 -m services.run_single Bookmarked --application-id app-123

  # Process all items in the Discovered queue:
  python3 -m services.run_single Discovered --all
"""

import argparse
import json
import sys

from .runtime_config import load
from .stage_runner import run_all




def run(state, application_id=None, all_items=False, stage_name=None):
    """Process one or more applications from the given state."""
    return run_all(
        state, stage_name=stage_name,
        workers=1,
        print_progress=True, application_id=application_id,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "queue",
        help="Application state/queue to process (e.g. Discovered, Bookmarked, Validated)"
    )
    parser.add_argument(
        "--application-id",
        help="Process only this specific application"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all eligible applications (also the default; retained for compatibility)"
    )
    parser.add_argument(
        "--stage",
        help="Stage name to use (needed when multiple stages consume the same state)"
    )
    args = parser.parse_args(argv)

    load(require=True)
    try:
        results = run(args.queue, args.application_id, args.all, args.stage)
        return 1 if any(r.get("status") == "failed" for r in results) else 0
    except (ValueError, RuntimeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
