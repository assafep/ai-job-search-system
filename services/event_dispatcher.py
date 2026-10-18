"""Pure event-to-workflow routing.

Adapters create normalized events; this dispatcher only chooses a workflow and
creates a workload envelope. It does not run the workflow or perform effects.
"""

import hashlib
import argparse
import json
import sys
import uuid


ROUTES = {
    "job_discovery_bookmark_schedule": "discover-and-bookmark",
    "application_package_schedule": "prepare-bookmarked",
    "application_submission_schedule": "submit-ready",
    "calendar_event": "prepare-interview",
    "incoming_email": "handle-incoming-email",
    "reconciliation_schedule": "reconcile",
}


def dispatch(event_type, payload, event_id=None, registry=None):
    if event_type not in ROUTES:
        raise ValueError("unhandled event type: " + event_type)
    normalized_event_id = event_id or "event_" + uuid.uuid4().hex
    workload_id = "work_" + hashlib.sha256(
        (event_type + ":" + normalized_event_id).encode("utf-8")
    ).hexdigest()[:24]
    envelope = {
        "workload_id": workload_id,
        "workflow": ROUTES[event_type],
        "event": {
            "event_id": normalized_event_id,
            "event_type": event_type,
            "payload": payload,
        },
        "status": "queued",
    }
    if registry is not None:
        registry.enqueue(envelope)
    return envelope


def dispatch_normalized(event, registry=None):
    """Route an adapter contract produced by ``adapters.contracts``."""
    if not isinstance(event, dict):
        raise ValueError("normalized event must be an object")
    for field in ("event_type", "event_id", "payload"):
        if field not in event:
            raise ValueError("normalized event is missing " + field)
    if not isinstance(event["event_type"], str) or not event["event_type"].strip():
        raise ValueError("normalized event_type must be a non-empty string")
    if not isinstance(event["event_id"], str) or not event["event_id"].strip():
        raise ValueError("normalized event_id must be a non-empty string")
    if not isinstance(event["payload"], dict):
        raise ValueError("normalized event payload must be an object")
    return dispatch(event["event_type"], event["payload"], event["event_id"], registry)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Route one normalized adapter event")
    parser.add_argument("--event-file", required=True, help="JSON normalized adapter event")
    parser.add_argument(
        "--enqueue",
        action="store_true",
        help="persist the queued workload using WORKLOAD_REGISTRY_PATH",
    )
    args = parser.parse_args(argv)
    try:
        with open(args.event_file) as handle:
            event = json.load(handle)
        registry = None
        if args.enqueue:
            from .workload_registry import registry_from_environment
            registry = registry_from_environment()
        envelope = dispatch_normalized(event, registry)
    except (IOError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(envelope, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
