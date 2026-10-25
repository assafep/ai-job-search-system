"""Consume all application queues driven by workflow blueprints.

Single-consumer architecture: the main thread reads from the Application Service,
enriches context, dispatches agent work to a capped thread pool, and writes
results back. Worker threads never touch the Application Service.

Discovers queue-driven workflows from their blueprints (non-empty input_states),
builds a state-to-workflow map, and processes each pipeline stage with eight
posting workers and serialized storage.

Pipeline order (from blueprint prefixes):
  Discovered → bookmark-discovered → Bookmarked
  Bookmarked → validate-bookmarked → Validated / completed as invalidated
  Validated → prepare-validated → Prepared / Manual Apply Required
  Prepared → submit-prepared → Applied / Manual Apply Required
"""

import argparse
import fcntl
import json
import os
from collections import Counter
from datetime import datetime, timezone
from threading import Lock

from adapters.llm.codex_provider import provider
from application_service.storage_layout import workload_state_path
from .activity_report import publish
from .application_service_client import ApplicationServiceClient
from .report_checkpoint import ReportCheckpoint
from .runtime_config import load, ROOT
from .test_runner import discover_workflows, parse_workflow
from .workflow_runtime import runtime_from_environment
from .workflow_runner import run_one
from .workload_registry import registry_from_environment


def _discover_queue_workflows():
    """Discover workflows that consume from application states.

    Returns a dict mapping state name to workflow definition, sorted by
    blueprint filename (00-discover, 01-bookmark-discovered, etc.).
    """
    result = {}
    for name, path in discover_workflows().items():
        definition = parse_workflow(path)
        if definition.get("trigger", {}).get("poll_kind") != "queue":
            continue
        input_states = definition.get("input_states") or []
        if not input_states:
            continue
        event_type = definition.get("trigger", {}).get("event_type")
        if event_type is None:
            continue
        for state in input_states:
            result[state] = {
                "workflow": name,
                "event_type": event_type,
                "outcomes": definition.get("outcomes", {}),
            }
    return result


def run(persistence, registry, runtime, agent_provider, workers=8, *, workflows=None, application_ids=None, limit=None, run_id=None, code_revision=None):
    if workers != 8:
        raise ValueError('this queue runner requires exactly eight posting workers')

    queue_workflows = _discover_queue_workflows()
    queue_workflows = {state: info for state, info in queue_workflows.items()
                       if workflows is None or info["workflow"] in workflows}
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer")
    if not queue_workflows:
        raise ValueError("no queue-driven workflows found in blueprints")

    # Snapshot all applications in states that have consuming workflows
    snapshot = persistence.list_applications()
    initial = [a for a in snapshot if a["status"] in queue_workflows]
    # Defensive deduplication: a posting can never be assigned twice.
    initial = list({a["application_id"]: a for a in initial}.values())

    if application_ids is not None:
        selected = set(application_ids)
        initial = [a for a in initial if a["application_id"] in selected]
    if limit is not None:
        initial = initial[:limit]
    run_id = run_id or datetime.now(timezone.utc).strftime('queues-%Y%m%dT%H%M%S%fZ')
    report = {
        "run_id": run_id,
        "code_revision": code_revision,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "workers": workers,
        "queue_workflows": {state: info["workflow"] for state, info in queue_workflows.items()},
        "initial_applications": [
            {"application_id": a["application_id"], "company": a["company"],
             "role": a["role"], "state": a["status"]}
            for a in initial
        ],
        "workload_results": [],
        "posting_results": [],
    }
    state_path = workload_state_path('system-run', run_id + '.json')
    checkpoint = ReportCheckpoint(persistence, state_path, report)
    print_lock = Lock()

    def emit(value):
        with print_lock:
            print(json.dumps(value, ensure_ascii=False, default=str), flush=True)

    def consume(application):
        app_id = application["application_id"]
        state = application["status"]

        emit({
            "activity": "started",
            "application_id": app_id,
            "company": application["company"],
            "state": state,
        })

        # Resolve workflow for this application's state
        if state not in queue_workflows:
            final = persistence.get_application(app_id)
            emit({
                "activity": "skipped",
                "application_id": app_id,
                "state": state,
                "reason": "no consuming workflow",
            })
            return {
                "application_id": app_id,
                "final_state": final["status"],
                "workload_results": [],
            }

        queue_info = queue_workflows[state]
        workflow_name = queue_info["workflow"]

        result = run_one(
            persistence,
            registry,
            runtime,
            application,
            workflow_name=workflow_name,
            agent_provider=agent_provider,
            run_id=run_id + ":" + workflow_name + ":" + app_id,
        )

        final_state = result.get("final_state", "unknown")
        status_label = result.get("status", "unknown")
        emit({
            "activity": "finished" if status_label != "failed" else "error",
            "application_id": app_id,
            "company": application["company"],
            "workflow": workflow_name,
            "status": final_state,
        })

        return {
            "application_id": app_id,
            "final_state": final_state,
            "workload_results": [result],
        }

    from concurrent.futures import ThreadPoolExecutor, as_completed

    checkpoint.save(force=True)
    emit({
        "run_id": run_id,
        "workers": workers,
        "states": dict(Counter(a["status"] for a in initial)),
        "queue_workflows": {state: info["workflow"] for state, info in queue_workflows.items()},
    })
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="posting") as pool:
            futures = [pool.submit(consume, app) for app in initial]
            for future in as_completed(futures):
                outcome = future.result()
                report["posting_results"].append(
                    {k: v for k, v in outcome.items() if k != "workload_results"}
                )
                report["workload_results"].extend(outcome["workload_results"])
                checkpoint.save()
    except BaseException:
        checkpoint.save(force=True)
        raise

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["processed_postings"] = len(report["posting_results"])
    report["final_application_counts"] = dict(Counter(a["status"] for a in persistence.list_applications()))
    report["workload_counts"] = dict(Counter(r.get("workflow", "unknown") for r in report["workload_results"]))
    report["storage_synchronization"] = persistence.storage_statistics()
    report["checkpoint_uploads"] = checkpoint.uploads + 1
    report["activity_report_pdf"] = publish(persistence, report)
    checkpoint.save(force=True)
    emit({
        "activity": "complete",
        "processed": report["processed_postings"],
        "storage_synchronization": report["storage_synchronization"],
        "activity_report_pdf": report["activity_report_pdf"],
        "final_counts": report["final_application_counts"],
    })
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    parser.error('Use the ChatGPT native-browser MCP task tools for Bookmarked and Prepared; this CLI cannot execute those workflows')


if __name__ == '__main__':
    main()
