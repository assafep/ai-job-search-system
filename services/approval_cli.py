"""Operator CLI for the durable human-approval registry."""

import argparse
from dataclasses import asdict
import json
import sys

from .approval_registry import approval_registry_from_environment


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect and decide workflow approvals")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_command = subparsers.add_parser("list", help="list pending approval requests")
    list_command.add_argument("--all", action="store_true", help="include decided requests")

    request_command = subparsers.add_parser("request", help="create an approval request")
    request_command.add_argument("action")
    request_command.add_argument("workload_id")
    request_command.add_argument("--context", default="{}")
    request_command.add_argument("--idempotency-key")

    decide_command = subparsers.add_parser("decide", help="approve or reject a request")
    decide_command.add_argument("request_id")
    decide_command.add_argument("decision", choices=("approved", "rejected"))
    decide_command.add_argument("--note", default="")

    args = parser.parse_args(argv)
    registry = approval_registry_from_environment()
    try:
        if args.command == "list":
            records = registry.records.values() if args.all else registry.pending()
            result = [asdict(record) for record in records]
        elif args.command == "request":
            context = json.loads(args.context)
            if not isinstance(context, dict):
                raise ValueError("--context must be a JSON object")
            result = asdict(registry.request(
                args.action, args.workload_id, context, args.idempotency_key))
        else:
            result = asdict(registry.decide(args.request_id, args.decision, args.note))
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
