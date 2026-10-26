"""Workload planning, dry-run CLI, and explicit persistence execution.

The default CLI path is side-effect free. The ``execute`` helper is the
explicit persistence boundary used by ``StageExecutor`` after stateless
agent results have been validated; it does not call external adapters or an
LLM itself.
"""

import argparse
import json
import sys

from .test_runner import AGENTS, read_json, resolve
from .stage_definitions import STAGES, parse_stage
from .approval_service import evaluate
from .workload_executor import plan_persistence_writes
from .workload_executor import execute_persistence_writes
from .runtime_config import OnboardingRequired
from application_service.profile import calendly_url as profile_calendly_url
from application_service.journal_format import ALL_STATES
from application_service.state_machine import ALLOWED_TRANSITIONS


def _get_agents(stage_name):
    """Return the list of agent names for a stage from its blueprint."""
    definition = parse_stage(STAGES[stage_name])
    agent = definition.get("agent")
    if agent is None:
        return []
    return [agent]


def _get_external_actions(stage_name):
    """Return the list of external actions for a stage from its blueprint."""
    definition = parse_stage(STAGES[stage_name])
    return definition.get("external_actions", [])


def failure_outcome(stage_name, error):
    """Pick the blueprint outcome for a runner exception.

    Transport and persistence failures use ``system_error``. Any other
    exception uses the stage's ordinary failure outcome, ``failed`` or
    ``read_failure``, when the blueprint declares one.
    """
    from urllib import error as urllib_error
    from .application_service_client import ApplicationServiceError
    from .scout_reading import PostingReadFailure
    definition = parse_stage(STAGES[stage_name]) if stage_name in STAGES else {}
    outcomes = definition.get('outcomes') or {}
    system = isinstance(error, (
        urllib_error.URLError, TimeoutError, ConnectionError, OSError, ApplicationServiceError))
    if not system and isinstance(error, PostingReadFailure) and outcomes.get('read_failure'):
        key = 'read_failure'
    elif not system and outcomes.get('failed'):
        key = 'failed'
    elif not system and outcomes.get('read_failure'):
        key = 'read_failure'
    else:
        key = 'system_error'
    target = outcomes.get(key) or outcomes.get('system_error') or 'Dead Letters Queue'
    if key != 'system_error' and not outcomes.get(key):
        key = 'system_error'
    return key, target


def validate_stage_outcomes(stage_name, definition):
    """Validate that stage outcomes are exhaustive and map to allowed state transitions.

    Checks:
    - Every input state can transition to each non-null outcome target.
    - system_error outcome routes to Dead Letters Queue (or is absent for agentless stages).
    - All outcome targets are valid states.
    """
    input_states = definition.get("input_states", [])
    outcomes = definition.get("outcomes", {})
    if not outcomes:
        raise ValueError("stage {} must declare outcomes".format(stage_name))
    for outcome, target in outcomes.items():
        if target is not None and target not in ALL_STATES:
            raise ValueError(
                "stage {} outcome '{}' targets invalid state '{}'".format(stage_name, outcome, target))
    if "system_error" in outcomes and outcomes["system_error"] != "Dead Letters Queue":
        raise ValueError(
            "stage {} system_error must route to Dead Letters Queue".format(stage_name))
    for state in input_states:
        for outcome, target in outcomes.items():
            if target is None:
                continue
            # A receipt only advances submissions. Later receipts preserve the
            # existing Applied/Interviewing state (see correspondence writes).
            if (stage_name == 'handle-incoming-email' and outcome == 'confirmed'
                    and state not in {'Prepared', 'Manual Apply Required'}):
                continue
            # Same-state transition is a safe no-op (matches state_machine.validate_transition)
            if target == state:
                continue
            if target not in ALLOWED_TRANSITIONS.get(state, set()):
                raise ValueError(
                    "stage {} outcome '{}' routes to '{}' which is not allowed from input state '{}'"
                    .format(stage_name, outcome, target, state))


def build_plan(stage_name, context):
    if stage_name not in STAGES:
        raise ValueError("unknown stage: " + stage_name)
    definition = parse_stage(STAGES[stage_name])
    validate_stage_outcomes(stage_name, definition)
    agents = _get_agents(stage_name)
    return {
        "mode": "workload-dry-run",
        "stage": stage_name,
        "trigger_context": context,
        "input_states": definition.get("input_states", []),
        "outcomes": definition.get("outcomes", {}),
        "agent_invocations": [
            {
                "agent": name,
                "prompt_file": AGENTS[name] + "/prompt.md",
                "output_schema": AGENTS[name] + "/output.schema.json",
                "side_effects": "disabled",
            }
            for name in agents
        ],
        "approval_requirements": evaluate(_get_external_actions(stage_name)),
        "side_effects": "disabled",
    }


def run(stage_name, context, agent_results=None):
    """Return a dry-run plan, optionally including persistence operations."""
    plan = build_plan(stage_name, context)
    if agent_results is not None:
        plan["persistence_operations"] = plan_persistence_writes(
            stage_name, context, agent_results
        )
    return plan


def execute(stage_name, context, agent_results, persistence_client):
    effective_context = dict(context)
    if not effective_context.get("calendly_url") and hasattr(persistence_client, "get_profile"):
        profile = persistence_client.get_profile()
        if isinstance(profile, dict):
            effective_context["calendly_url"] = profile_calendly_url(profile)
    plan = run(stage_name, effective_context, agent_results)
    plan["execution"] = execute_persistence_writes(
        plan.get("persistence_operations", []), persistence_client, dry_run=False
    )
    return plan


def require_runtime_configuration():
    """Guard the explicit persistence execution path with onboarding state."""
    from .runtime_config import load
    return load(require=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dry-run one stage and its agent invocations")
    parser.add_argument("stage", choices=sorted(STAGES))
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
            expected_agents = set(_get_agents(args.stage))
            supplied_agents = set(agent_results)
            unknown_agents = supplied_agents - expected_agents
            if unknown_agents:
                raise ValueError(
                    "--agent-results contains unexpected agents: " + ", ".join(sorted(unknown_agents)))
            if supplied_agents and not supplied_agents.intersection(expected_agents):
                raise ValueError("--agent-results does not contain an agent used by this stage")
        context = read_json(resolve(args.fixture))
        if args.execute:
            if agent_results is None:
                raise ValueError("--execute requires --agent-results")
            expected_agents = set(_get_agents(args.stage))
            missing_agents = expected_agents - set(agent_results)
            if missing_agents:
                raise ValueError(
                    "--execute is missing agent results: " + ", ".join(sorted(missing_agents)))
            require_runtime_configuration()
            from .application_service_client import ApplicationServiceClient
            result = execute(args.stage, context, agent_results, ApplicationServiceClient())
        else:
            result = run(args.stage, context, agent_results)
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
