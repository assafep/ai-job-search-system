"""Common logic for running a workflow on one or more applications.

Architecture: single-consumer with a capped thread pool.

- The main consumer thread is the ONLY code that reads from or writes to the
  Application Service. It lists applications, enriches context, and executes
  persistence writes.
- Worker threads are stateless: they receive pre-enriched context, invoke the
  workflow's agent(s), and return the results. They never touch the persistence
  API.
- A bounded ``queue.Queue`` feeds work to the thread pool. As soon as a worker
  returns, the main consumer hands it a new item.

Every application is processed independently: a failure on one item never
blocks the rest of the queue.
"""

import json
import os
import queue
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from time import monotonic

from .event_dispatcher import dispatch
from .application_service_client import ApplicationServiceClient, ApplicationServiceError
from .test_runner import discover_workflows, parse_workflow
from .workflow_runtime import runtime_from_environment
from .workload_executor import execute_persistence_writes, plan_persistence_writes
from .workload_registry import registry_from_environment
from .workload_runner import failure_outcome


def _run_with_workflow_python(python, application, workflow_name, run_id, root):
    """Execute this workload in the workflow virtualenv and return its result."""
    env = dict(os.environ)
    env["WORKFLOW_VENV_DELEGATED"] = "1"
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [python, "-m", "services.workflow_venv"],
        input=json.dumps({
            "application": application,
            "workflow_name": workflow_name,
            "run_id": run_id,
        }),
        text=True,
        capture_output=True,
        cwd=root,
        env=env,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "workflow virtualenv failed").strip()
        return {
            "application_id": application.get("application_id"),
            "workflow": workflow_name,
            "initial_state": application.get("status"),
            "final_state": application.get("status"),
            "status": "failed",
            "error": detail,
        }
    return json.loads(completed.stdout)


def _workflow_for_state(state):
    """Find the workflow that consumes a given state from its input_states."""
    results = []
    for name, path in discover_workflows().items():
        definition = parse_workflow(path)
        input_states = definition.get("input_states", [])
        if state in input_states:
            results.append((name, definition))
    if not results:
        return None, None
    if len(results) == 1:
        return results[0]
    for name, definition in results:
        if definition.get("trigger", {}).get("event_type"):
            return name, definition
    return results[0]


def _get_provider(workflow_name):
    """Load the appropriate LLM provider for the workflow's agent."""
    definition = parse_workflow(next(
        path for name, path in discover_workflows().items() if name == workflow_name
    ))
    agent = definition.get("agent")
    if agent in ("read-posting", "validate"):
        from .worker_main import load_discovery_provider
        return load_discovery_provider()
    from .worker_main import load_provider
    return load_provider()


def _agent_worker(runtime, workflow_name, enriched_context, agent_provider):
    """Worker function: invoke agent(s) on pre-enriched context.

    This runs in a worker thread. It does NOT touch the Application Service.
    """
    try:
        agent_results = runtime.run_agent(workflow_name, enriched_context, agent_provider)
        return {"status": "ok", "agent_results": agent_results}
    except Exception as exc:
        return {"status": "error", "error": exc}


def _bookmark_attempt(agent_results):
    if isinstance(agent_results, dict):
        return agent_results.get("bookmark_attempt")
    return None


def _with_bookmark_attempt(error, attempt):
    """Keep the bookmark model record on a later persistence failure."""
    if attempt and not getattr(error, "attempt", None):
        try:
            error.attempt = attempt
        except Exception:
            pass
    return error


def run_one(
    persistence,
    registry,
    runtime,
    application,
    workflow_name=None,
    agent_provider=None,
    run_id=None,
):
    """Run a single application through its consuming workflow.

    The caller (main thread) handles all persistence reads and writes.
    Agent invocation is dispatched to a single worker thread.

    Args:
        persistence: ApplicationServiceClient instance.
        registry: WorkloadRegistry instance.
        runtime: WorkflowRuntime instance.
        application: Dict with application_id, company, role, status.
        workflow_name: Optional override; auto-detected from state if omitted.
        agent_provider: LLM provider callable.
        run_id: Run identifier for workload naming.

    Returns:
        Dict with application_id, workflow, initial_state, final_state, status.
    """
    app_id = application["application_id"]
    company = application.get("company", "")
    role = application.get("role", "")
    initial_state = application["status"]

    # Resolve workflow from state if not explicitly provided
    if workflow_name is None:
        workflow_name, definition = _workflow_for_state(initial_state)
        if workflow_name is None:
            return {
                "application_id": app_id,
                "workflow": None,
                "initial_state": initial_state,
                "final_state": initial_state,
                "status": "skipped",
                "reason": "no consuming workflow",
            }
    else:
        workflows = discover_workflows()
        if workflow_name not in workflows:
            raise ValueError("unknown workflow: " + workflow_name)
        definition = parse_workflow(workflows[workflow_name])

    if agent_provider is None and os.environ.get("WORKFLOW_VENV_DELEGATED") != "1":
        from .workflow_venv import ROOT, ensure_workflow_python
        python = ensure_workflow_python(workflow_name)
        if python and os.path.realpath(python) != os.path.realpath(sys.executable):
            return _run_with_workflow_python(python, application, workflow_name, run_id, ROOT)

    event_type = definition.get("trigger", {}).get("event_type")
    if event_type is None:
        raise ValueError("workflow {} has no event type".format(workflow_name))

    # Resolve agent provider if not provided
    if agent_provider is None:
        agent = definition.get("agent")
        agent_provider = _get_provider(workflow_name) if agent else None

    identity = run_id or "single-{}-{}".format(
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
        app_id,
    )

    payload = {"application_id": app_id}
    if company:
        payload["company"] = company
    if role:
        payload["role"] = role

    started = monotonic()
    agent_results = {}

    # --- Main thread: all persistence reads ---
    # Re-read current state to check for staleness
    try:
        current = persistence.get_application(app_id)
        current_state = current.get("status") if isinstance(current, dict) else current.status
    except (KeyError, ApplicationServiceError) as exc:
        if "404" in str(exc):
            return {
                "application_id": app_id,
                "workflow": workflow_name,
                "initial_state": initial_state,
                "final_state": "unknown",
                "status": "skipped",
                "reason": "application not found",
                "elapsed_seconds": monotonic() - started,
            }
        raise

    if current_state != initial_state:
        return {
            "application_id": app_id,
            "workflow": workflow_name,
            "initial_state": initial_state,
            "final_state": current_state,
            "status": "skipped",
            "reason": "state changed by another worker",
            "elapsed_seconds": monotonic() - started,
        }

    # Enrich context from persistence (main thread only)
    enriched_context = runtime.prepare_context(
        workflow_name, payload, agent_provider
    )
    enriched_context["workload_id"] = identity

    # Create workload envelope
    envelope = dispatch(event_type, payload, identity, registry)
    workload_id = envelope["workload_id"]

    try:
        # --- Worker thread: stateless agent invocation ---
        if definition.get("agent"):
            agent_result = _agent_worker(
                runtime, workflow_name, enriched_context, agent_provider
            )
            if agent_result["status"] == "error":
                error = agent_result["error"]
                raise error if isinstance(error, BaseException) else RuntimeError(str(error))
            agent_results = agent_result["agent_results"]
        else:
            # No agent (e.g., discover workflow) — agent_results is empty
            agent_results = {}

        # --- Main thread: plan and execute persistence writes ---
        operations = plan_persistence_writes(workflow_name, enriched_context, agent_results)
        execute_persistence_writes(operations, persistence, dry_run=False)

        registry.claim(workload_id)
        registry.complete(workload_id)
        final = persistence.get_application(app_id)
        final_state = final.get("status") if isinstance(final, dict) else final.status
        return {
            "application_id": app_id,
            "workflow": workflow_name,
            "initial_state": initial_state,
            "final_state": final_state,
            "status": "succeeded",
            "elapsed_seconds": monotonic() - started,
        }
    except Exception as exc:
        _with_bookmark_attempt(exc, _bookmark_attempt(agent_results))
        try:
            registry.fail(workload_id, str(exc))
        except ValueError:
            # Workload was already completed or not in running state
            pass
        return _handle_failure(
            persistence, app_id, workflow_name, initial_state, exc, started
        )


def _failure_note(workflow_name, error):
    outcome, target = failure_outcome(workflow_name, error if isinstance(error, BaseException) else RuntimeError(str(error)))
    label = "System error" if outcome == "system_error" else outcome
    return outcome, target, label + ": " + str(error)


def _handle_failure(persistence, app_id, workflow_name, initial_state, error, started):
    """Move the posting to the failure state declared by its blueprint."""
    _outcome, target, note = _failure_note(workflow_name, error)
    from .event_attempt import with_attempt
    bullets = with_attempt([note], getattr(error, "attempt", None))
    try:
        persistence.transition(
            app_id,
            target,
            {
                "source": workflow_name + " workflow",
                "bullets": bullets,
                "attempt": getattr(error, "attempt", None) or {},
            },
            "workflow-failure-" + app_id,
        )
        final_state = target
    except Exception:
        try:
            final = persistence.get_application(app_id)
            final_state = final.get("status") if isinstance(final, dict) else final.status
        except Exception:
            final_state = "unknown"

    return {
        "application_id": app_id,
        "workflow": workflow_name,
        "initial_state": initial_state,
        "final_state": final_state,
        "status": "failed",
        "error": error,
        "elapsed_seconds": monotonic() - started,
    }


def run_all(
    state,
    workflow_name=None,
    workers=8,
    agent_provider=None,
    print_progress=True,
    application_id=None,
    limit=None,
):
    """Run all applications in a state through their consuming workflow.

    Single-consumer architecture:
    - Main thread lists applications, enriches context, and executes writes.
    - Worker threads only invoke agents on pre-enriched context.
    - A bounded queue feeds work to the thread pool with backpressure.

    Args:
        state: Application state/queue name (e.g. "Bookmarked").
        workflow_name: Optional workflow override.
        workers: Number of parallel worker threads (default 8).
        agent_provider: Optional LLM provider; auto-loaded if omitted.
        print_progress: Print JSON progress events per item.

    Returns:
        List of result dicts, one per application.
    """
    from application_service.workflow_execution import CHATGPT_WORKFLOWS
    if agent_provider is None and (state in {'Bookmarked', 'Prepared'} or workflow_name in CHATGPT_WORKFLOWS):
        raise ValueError('Validation and submission require ChatGPT native browser execution through MCP workflow task tools')
    from .report_checkpoint import ReportCheckpoint
    from .runtime_config import load
    from application_service.storage_layout import workload_state_path

    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be a positive integer")
    load(require=True)

    persistence = ApplicationServiceClient()
    registry = registry_from_environment()
    runtime = runtime_from_environment(persistence_client=persistence)
    runtime.registry = registry

    # Resolve workflow
    if workflow_name is None:
        workflow_name, definition = _workflow_for_state(state)
        if workflow_name is None:
            raise ValueError("no workflow consumes state: " + state)
    else:
        workflows = discover_workflows()
        if workflow_name not in workflows:
            raise ValueError("unknown workflow: " + workflow_name)
        definition = parse_workflow(workflows[workflow_name])

    # --- Main thread: list applications ---
    if state not in definition.get("input_states", []):
        raise ValueError("workflow does not consume requested state")
    applications = persistence.list_applications(status=state)
    if application_id is not None:
        applications = [a for a in applications if a["application_id"] == application_id]
        if not applications:
            raise ValueError("application not found in requested state")
    applications = list({a["application_id"]: a for a in applications}.values())
    if limit is not None:
        applications = applications[:limit]
    if not applications:
        if print_progress:
            print(
                json.dumps(
                    {"status": "idle", "message": "no applications in state " + state},
                    indent=2,
                )
            )
        return []

    # Deduplicate by application_id
    applications = list({a["application_id"]: a for a in applications}.values())

    # Resolve agent provider
    if agent_provider is None:
        agent = definition.get("agent")
        agent_provider = _get_provider(workflow_name) if agent else None

    run_id = datetime.now(timezone.utc).strftime("wf-%Y%m%dT%H%M%S%fZ")
    report = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "workflow": workflow_name,
        "workers": workers,
        "initial_count": len(applications),
        "results": [],
    }
    state_path = workload_state_path("workflow-run", run_id + ".json")
    checkpoint = ReportCheckpoint(persistence, state_path, report)
    print_lock = threading.Lock()

    def emit(value):
        if print_progress:
            with print_lock:
                print(
                    json.dumps(value, ensure_ascii=False, default=str),
                    flush=True,
                )

    checkpoint.save(force=True)
    emit({
        "activity": "run_started",
        "workflow": workflow_name,
        "state": state,
        "count": len(applications),
        "workers": workers,
    })

    # --- Single-consumer, thread-pool pattern ---
    # Main thread enriches context; workers invoke agents; main thread writes results.

    def worker_task(item):
        """Process one application: agent invocation only."""
        app_id, enriched_context, initial_state = item
        company = enriched_context.get("company", "")
        role = enriched_context.get("role", "")

        agent_results = {}
        try:
            if definition.get("agent"):
                agent_result = _agent_worker(
                    runtime, workflow_name, enriched_context, agent_provider
                )
                if agent_result["status"] == "error":
                    error = agent_result["error"]
                    raise error if isinstance(error, BaseException) else RuntimeError(str(error))
                agent_results = agent_result["agent_results"]
            else:
                agent_results = {}

            # Plan persistence writes (no execution yet)
            operations = plan_persistence_writes(workflow_name, enriched_context, agent_results)
            return {
                "application_id": app_id,
                "company": company,
                "role": role,
                "initial_state": initial_state,
                "status": "ok",
                "operations": operations,
                "attempt": _bookmark_attempt(agent_results),
            }
        except Exception as exc:
            _with_bookmark_attempt(exc, _bookmark_attempt(agent_results))
            return {
                "application_id": app_id,
                "company": company,
                "role": role,
                "initial_state": initial_state,
                "status": "error",
                "error": exc,
                "attempt": getattr(exc, "attempt", None),
                "operations": [],
            }

    try:
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="wf-agent"
        ) as pool:
            futures = {}
            for app in applications:
                app_id = app["application_id"]
                initial_state = app["status"]
                company = app.get("company", "")
                role = app.get("role", "")

                # --- Main thread: re-read state and enrich context ---
                try:
                    current = persistence.get_application(app_id)
                    current_state = current.get("status") if isinstance(current, dict) else current.status
                except (KeyError, ApplicationServiceError) as exc:
                    if "404" in str(exc):
                        emit({
                            "activity": "skipped",
                            "workflow": workflow_name,
                            "application_id": app_id,
                            "company": company,
                            "role": role,
                            "reason": "application not found",
                        })
                        report["results"].append({
                            "application_id": app_id,
                            "workflow": workflow_name,
                            "initial_state": initial_state,
                            "final_state": "unknown",
                            "status": "skipped",
                            "reason": "application not found",
                        })
                        checkpoint.save()
                        continue

                if current_state != initial_state:
                    emit({
                        "activity": "skipped",
                        "workflow": workflow_name,
                        "application_id": app_id,
                        "company": company,
                        "role": role,
                        "reason": "state changed",
                    })
                    report["results"].append({
                        "application_id": app_id,
                        "workflow": workflow_name,
                        "initial_state": initial_state,
                        "final_state": current_state,
                        "status": "skipped",
                        "reason": "state changed by another worker",
                    })
                    checkpoint.save()
                    continue

                # Enrich context from persistence (main thread)
                payload = {"application_id": app_id}
                if company:
                    payload["company"] = company
                if role:
                    payload["role"] = role

                enriched_context = runtime.prepare_context(
                    workflow_name, payload, agent_provider
                )
                enriched_context["workload_id"] = run_id + ":" + workflow_name + ":" + app_id

                # Dispatch to worker thread via bounded queue
                future = pool.submit(worker_task, (app_id, enriched_context, initial_state))
                futures[future] = app

            # Collect results as workers complete
            for future in as_completed(futures):
                app = futures[future]
                app_id = app["application_id"]
                company = app.get("company", "")
                role = app.get("role", "")

                try:
                    worker_result = future.result()
                except Exception as exc:
                    worker_result = {
                        "application_id": app_id,
                        "company": company,
                        "role": role,
                        "initial_state": app["status"],
                        "status": "error",
                        "error": str(exc),
                        "operations": [],
                    }

                if worker_result["status"] == "error":
                    # --- Main thread: handle failure ---
                    error = worker_result.get("error", "unknown")
                    _with_bookmark_attempt(error, worker_result.get("attempt"))
                    _outcome, target, note = _failure_note(workflow_name, error)
                    from .event_attempt import with_attempt
                    try:
                        persistence.transition(
                            app_id,
                            target,
                            {
                                "source": workflow_name + " workflow",
                                "bullets": with_attempt([note], getattr(error, "attempt", None)),
                                "attempt": getattr(error, "attempt", None) or {},
                            },
                            "workflow-failure-" + app_id,
                        )
                        final_state = target
                    except Exception:
                        try:
                            final = persistence.get_application(app_id)
                            final_state = final.get("status") if isinstance(final, dict) else final.status
                        except Exception:
                            final_state = "unknown"

                    emit({
                        "activity": "error",
                        "workflow": workflow_name,
                        "application_id": app_id,
                        "company": company,
                        "role": role,
                        "error": error,
                    })
                    report["results"].append({
                        "application_id": app_id,
                        "workflow": workflow_name,
                        "initial_state": worker_result.get("initial_state"),
                        "final_state": final_state,
                        "status": "failed",
                        "error": error,
                    })
                    checkpoint.save()
                    continue

                # --- Main thread: execute persistence writes ---
                try:
                    operations = worker_result["operations"]
                    execute_persistence_writes(operations, persistence, dry_run=False)

                    final = persistence.get_application(app_id)
                    final_state = final.get("status") if isinstance(final, dict) else final.status

                    emit({
                        "activity": "finished",
                        "workflow": workflow_name,
                        "application_id": app_id,
                        "company": company,
                        "role": role,
                        "initial_state": worker_result.get("initial_state"),
                        "final_state": final_state,
                        "status": "succeeded",
                    })
                    report["results"].append({
                        "application_id": app_id,
                        "workflow": workflow_name,
                        "initial_state": worker_result.get("initial_state"),
                        "final_state": final_state,
                        "status": "succeeded",
                    })
                except Exception as exc:
                    # Persistence write failure
                    _with_bookmark_attempt(exc, worker_result.get("attempt"))
                    from .event_attempt import with_attempt
                    try:
                        persistence.transition(
                            app_id,
                            "Dead Letters Queue",
                            {
                                "source": workflow_name + " workflow",
                                "bullets": with_attempt(
                                    ["Persistence error: " + str(exc)], getattr(exc, "attempt", None)),
                                "attempt": getattr(exc, "attempt", None) or {},
                            },
                            "system-error-" + app_id,
                        )
                        final_state = "Dead Letters Queue"
                    except Exception:
                        final_state = "unknown"

                    emit({
                        "activity": "error",
                        "workflow": workflow_name,
                        "application_id": app_id,
                        "company": company,
                        "role": role,
                        "error": str(exc),
                    })
                    report["results"].append({
                        "application_id": app_id,
                        "workflow": workflow_name,
                        "initial_state": worker_result.get("initial_state"),
                        "final_state": final_state,
                        "status": "failed",
                        "error": str(exc),
                    })
                checkpoint.save()
    except BaseException:
        checkpoint.save(force=True)
        raise

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["success_count"] = sum(
        1 for r in report["results"] if r.get("status") in ("succeeded", "skipped")
    )
    report["failure_count"] = sum(
        1 for r in report["results"] if r.get("status") == "failed"
    )
    checkpoint.save(force=True)

    emit({
        "activity": "run_complete",
        "workflow": workflow_name,
        "processed": len(report["results"]),
        "succeeded": report["success_count"],
        "failed": report["failure_count"],
    })

    return report["results"]
