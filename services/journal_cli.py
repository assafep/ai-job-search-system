"""Operator CLI for inspecting and explicitly repairing journal state tabs."""

import argparse
import json
import sys

from .persistence_client import PersistenceApiClient, PersistenceApiError


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Inspect or repair journal state tabs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="read journal state-tab status")
    migrate = subparsers.add_parser('migrate-application-states', help='rename legacy ready tab and add manual and dead-letter queues')
    migrate.add_argument('--apply', action='store_true', help='explicitly apply the lifecycle migration')
    repair = subparsers.add_parser("repair", help="repair missing state tabs")
    repair.add_argument(
        "--apply",
        action="store_true",
        help="perform the explicit repair; without this flag only report what is missing",
    )
    args = parser.parse_args(argv)
    client = client or PersistenceApiClient()
    try:
        if args.command == "status":
            result = client.journal_status()
        elif args.command == 'migrate-application-states':
            result = client.migrate_application_states() if args.apply else dict(client.journal_status(), migration_not_applied=True)
        elif args.apply:
            result = client.repair_journal()
        else:
            result = dict(client.journal_status())
            result["repair_not_applied"] = True
            result["proposed_operations"] = [
                {"operation": "add_top_level_state_tab", "title": state}
                for state in result.get("missing_states", [])
            ]
    except PersistenceApiError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.command == "repair" and args.apply and not result.get("valid", False):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
