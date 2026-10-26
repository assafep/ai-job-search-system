"""Process all Discovered applications through bookmark-discovered in parallel.

Usage:
  python3 -m services.bookmark_all
"""

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from .event_dispatcher import dispatch
from .application_service_client import ApplicationServiceClient
from .runtime_config import load
from .workflow_runtime import runtime_from_environment
from .workload_registry import registry_from_environment
from .stage_definitions import discover_stages, parse_stage
from .worker_main import load_discovery_provider


def _get_workflow():
    workflows = discover_stages()
    if "bookmark-discovered" not in workflows:
        raise ValueError("bookmark-discovered workflow not found")
    return parse_stage(workflows["bookmark-discovered"])


def _bookmark_one(app, event_type, registry, runtime, agent_provider):
    app_id = app["application_id"]
    company = app.get("company", "")
    role = app.get("role", "")

    identity = "bookmark-all-{}-{}".format(
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
        app_id
    )

    payload = {"application_id": app_id}
    if company:
        payload["company"] = company
    if role:
        payload["role"] = role

    print(json.dumps({
        "activity": "started",
        "workflow": "bookmark-discovered",
        "application_id": app_id,
        "company": company,
        "role": role,
    }, ensure_ascii=False), flush=True)

    envelope = dispatch(event_type, payload, identity, registry)
    try:
        outcome = runtime.run(envelope["workload_id"], agent_provider=agent_provider)
        persistence = runtime.persistence_client
        final = persistence.get_application(app_id)
        final_status = final.get("status") if isinstance(final, dict) else getattr(final, "status", None)
        result = {
            "application_id": app_id,
            "company": company,
            "role": role,
            "initial_state": "Discovered",
            "final_state": final_status,
            "status": outcome.get("status", "unknown"),
        }
        print(json.dumps({
            "activity": "finished",
            **result,
        }, ensure_ascii=False), flush=True)
        return result
    except Exception as exc:
        result = {
            "application_id": app_id,
            "company": company,
            "role": role,
            "initial_state": "Discovered",
            "status": "failed",
            "error": str(exc),
        }
        print(json.dumps({
            "activity": "error",
            **result,
        }, ensure_ascii=False), flush=True)
        return result


def run(max_workers=8):
    definition = _get_workflow()
    event_type = definition.get("trigger", {}).get("event_type")
    if event_type is None:
        raise ValueError("bookmark-discovered has no event type")

    persistence = ApplicationServiceClient()
    registry = registry_from_environment()
    runtime = runtime_from_environment(persistence_client=persistence)
    runtime.registry = registry

    agent_provider = load_discovery_provider()

    applications = persistence.list_applications(status="Discovered")
    if not applications:
        print(json.dumps({"status": "idle", "message": "no applications in Discovered"}, indent=2))
        return []

    print(json.dumps({
        "run": "bookmark-all",
        "workers": max_workers,
        "discovered_count": len(applications),
    }, ensure_ascii=False), flush=True)

    results = []
    lock = threading.Lock()

    def worker(app):
        # Each thread needs its own runtime instance to avoid shared state issues
        thread_runtime = runtime_from_environment(persistence_client=persistence)
        thread_runtime.registry = registry
        return _bookmark_one(app, event_type, registry, thread_runtime, agent_provider)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, app): app for app in applications}
        for future in as_completed(futures):
            result = future.result()
            with lock:
                results.append(result)

    # Summary
    bookmarked = sum(1 for r in results if r.get("final_state") == "Bookmarked")
    failed = sum(1 for r in results if r.get("status") == "failed")
    other = len(results) - bookmarked - failed

    print(json.dumps({
        "run": "bookmark-all-complete",
        "total": len(results),
        "bookmarked": bookmarked,
        "failed": failed,
        "other": other,
    }, ensure_ascii=False), flush=True)

    return results


def main(argv=None):
    load(require=True)
    try:
        results = run()
        return 1 if any(r.get("status") == "failed" for r in results) else 0
    except (ValueError, RuntimeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
