"""Workload planning, dry-run CLI, and explicit persistence execution.

The default CLI path is side-effect free. The ``execute`` helper is the
explicit persistence boundary used by ``WorkflowRuntime`` after stateless
agent results have been validated; it does not call external adapters or an
LLM itself.
"""

import argparse
import json
import sys

from .test_runner import AGENTS, WORKFLOWS, parse_workflow, read_json, resolve
from .approval_service import evaluate
from .workload_executor import plan_persistence_writes
from .workload_executor import execute_persistence_writes
from .runtime_config import OnboardingRequired
from persistence_api.profile import calendly_url as profile_calendly_url


WORKFLOW_AGENTS = {
    "submit-ready": [],
    "discover-and-apply": ["job-discovery", "application"],
    "discover-and-bookmark": ["job-discovery"],
    "prepare-bookmarked": ["application"],
    "prepare-interview": ["interview-prep"],
    "handle-incoming-email": ["correspondence"],
    "reconcile": [],
}

PERSISTENCE_ACTIONS = {
    "submit-ready": ["read_application", "read_application_package", "record_submission_receipt", "record_application_state"],
    "discover-and-apply": ["read_preferences", "upsert_postings", "record_application_state"],
    "discover-and-bookmark": ["read_preferences", "upsert_postings", "record_application_state"],
    "prepare-bookmarked": ["read_application", "write_application_package", "record_application_state"],
    "prepare-interview": ["read_application", "write_interview_briefing", "record_preparation_status"],
    "handle-incoming-email": ["read_application", "record_correspondence_event", "record_proposed_response"],
    "reconcile": ["read_pending_workloads", "record_reconciliation_result", "record_errors"],
}

EXTERNAL_ACTIONS = {
    "submit-ready": ["submit_job_application"],
    "discover-and-apply": ["submit_job_application"],
    "discover-and-bookmark": [],
    "prepare-bookmarked": [],
    "prepare-interview": [],
    "handle-incoming-email": ["send_recruitment_email"],
    "reconcile": [],
}


def build_plan(workflow_name, context):
    if workflow_name not in WORKFLOWS:
        raise ValueError("unknown workflow: " + workflow_name)
    definition = parse_workflow(WORKFLOWS[workflow_name])
    agents = WORKFLOW_AGENTS[workflow_name]
    return {
        "mode": "workload-dry-run",
        "workflow": workflow_name,
        "trigger_context": context,
        "workflow_steps": definition["steps"],
        "agent_invocations": [
            {
                "agent": name,
                "prompt_file": AGENTS[name] + "/prompt.md",
                "output_schema": AGENTS[name] + "/output.schema.json",
                "side_effects": "disabled",
            }
            for name in agents
        ],
        "proposed_persistence_actions": PERSISTENCE_ACTIONS[workflow_name],
        "approval_requirements": evaluate(EXTERNAL_ACTIONS[workflow_name]),
        "side_effects": "disabled",
    }


def run(workflow_name, context, agent_results=None):
    """Return a dry-run plan, optionally including persistence operations."""
    plan = build_plan(workflow_name, context)
    if agent_results is not None:
        plan["persistence_operations"] = plan_persistence_writes(
            workflow_name, context, agent_results
        )
    return plan


def execute(workflow_name, context, agent_results, persistence_client):
    effective_context = dict(context)
    if not effective_context.get("calendly_url") and hasattr(persistence_client, "get_profile"):
        profile = persistence_client.get_profile()
        if isinstance(profile, dict):
            effective_context["calendly_url"] = profile_calendly_url(profile)
    plan = run(workflow_name, effective_context, agent_results)
    plan["execution"] = execute_persistence_writes(
        plan.get("persistence_operations", []), persistence_client, dry_run=False
    )
    return plan


def require_runtime_configuration():
    """Guard the explicit persistence execution path with onboarding state."""
    from .runtime_config import load
    return load(require=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dry-run one workflow and its agent invocations")
    parser.add_argument("workflow", choices=sorted(WORKFLOWS))
    parser.add_argument("--fixture", required=True, help="JSON workload context fixture")
    parser.add_argument(
        "--agent-results",
        help="JSON mapping of agent names to captured results; persistence remains dry-run",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="execute planned persistence writes; requires onboarding and --agent-results",
    )
    args = parser.parse_args(argv)
    try:
        agent_results = read_json(resolve(args.agent_results)) if args.agent_results else None
        if agent_results is not None and not isinstance(agent_results, dict):
            raise ValueError("--agent-results must contain an object keyed by agent name")
        if agent_results is not None:
            expected_agents = set(WORKFLOW_AGENTS[args.workflow])
            supplied_agents = set(agent_results)
            unknown_agents = supplied_agents - expected_agents
            if unknown_agents:
                raise ValueError(
                    "--agent-results contains unexpected agents: " + ", ".join(sorted(unknown_agents)))
            if supplied_agents and not supplied_agents.intersection(expected_agents):
                raise ValueError("--agent-results does not contain an agent used by this workflow")
        context = read_json(resolve(args.fixture))
        if args.execute:
            if agent_results is None:
                raise ValueError("--execute requires --agent-results")
            expected_agents = set(WORKFLOW_AGENTS[args.workflow])
            missing_agents = expected_agents - set(agent_results)
            if missing_agents:
                raise ValueError(
                    "--execute is missing agent results: " + ", ".join(sorted(missing_agents)))
            require_runtime_configuration()
            from .persistence_client import PersistenceApiClient
            result = execute(args.workflow, context, agent_results, PersistenceApiClient())
        else:
            result = run(args.workflow, context, agent_results)
    except OnboardingRequired as exc:
        print(json.dumps({
            "valid": False,
            "error": str(exc),
            "onboarding_command": "python3 -m services.onboarding",
        }, indent=2))
        return 1
    except (IOError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
