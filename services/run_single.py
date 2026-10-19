"""Process a single application from a specific queue with real side effects.

Usage examples:
  # Process one item from the Discovered queue:
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
from .workflow_runner import run_all




def run(state, application_id=None, all_items=False, workflow_name=None):
    """Process one or more applications from the given state."""
    # For a single specific application, use sequential mode
    if application_id or not all_items:
        results = run_all(
            state,
            workflow_name=workflow_name,
            workers=1,
            print_progress=True,
        )
        # Filter to the requested application if specified
        if application_id:
            results = [r for r in results if r.get("application_id") == application_id]
            if not results:
                raise ValueError(
                    "application {} not found in state {}".format(application_id, state)
                )
        return results
    else:
        return run_all(
            state,
            workflow_name=workflow_name,
            workers=8,
            print_progress=True,
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
        help="Process all applications in the queue (default: one)"
    )
    parser.add_argument(
        "--workflow",
        help="Workflow name to use (needed when multiple workflows consume the same state)"
    )
    args = parser.parse_args(argv)

    load(require=True)
    try:
        results = run(args.queue, args.application_id, args.all, args.workflow)
        return 1 if any(r.get("status") == "failed" for r in results) else 0
    except (ValueError, RuntimeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
