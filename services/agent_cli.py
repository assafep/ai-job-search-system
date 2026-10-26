"""Standalone CLI for running a stateless agent and printing its raw output.

Usage examples:
  # Read one posting URL through the read-posting agent:
  python3 -m services.agent_cli read-posting --url https://example.com/job/123

  # Pass arbitrary JSON context to any agent:
  python3 -m services.agent_cli application --context tests/fixtures/workloads/prepare.json

The command loads the agent contract (prompt + schema), invokes the real LLM
provider, validates the output against the declared schema, and prints the
resulting JSON. No persistence writes, no side effects.
"""

import argparse
import json
import sys

from .agent_runner import run as run_agent, contract, AgentContractError
from .test_runner import AGENTS
from .runtime_config import load, OnboardingRequired
from .workload_effort import effort_for_stage


def build_context_from_url(url, agent_name):
    """Build a minimal agent context from a single URL."""
    if agent_name == "read-posting":
        return {"read_posting_url": url}
    raise ValueError("--url is only supported for read-posting agent")


def maybe_attach_profile(context):
    """If runtime is configured, fetch the applicant profile and attach it."""
    try:
        runtime = load(require=False)
        if runtime is None:
            return context
    except OnboardingRequired:
        return context
    try:
        from .application_service_client import ApplicationServiceClient
        client = ApplicationServiceClient()
        profile = client.get_profile()
        if isinstance(profile, dict):
            context["applicant_profile"] = profile
    except Exception:
        pass
    return context


def load_provider(agent_name):
    """Load the appropriate provider for the given agent."""
    if agent_name in ("read-posting", "validate"):
        from .worker_main import load_discovery_provider
        provider = load_discovery_provider()
        if hasattr(provider, "with_reasoning_effort"):
            provider = provider.with_reasoning_effort(effort_for_stage("bookmark-discovered"))
        return provider
    from .worker_main import load_provider
    return load_provider()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run a stateless agent and print its raw JSON output")
    parser.add_argument("agent", choices=sorted(AGENTS), help="Agent to run")
    parser.add_argument("--url", help="Posting URL (read-posting agent only)")
    parser.add_argument("--context", help="JSON file with agent context")
    parser.add_argument("--profile", action="store_true", default=True,
                        help="Attach applicant profile from Application Service (default: on)")
    parser.add_argument("--no-profile", dest="profile", action="store_false",
                        help="Do not fetch applicant profile")
    args = parser.parse_args(argv)

    if not args.url and not args.context:
        parser.error("one of --url or --context is required")
    if args.url and args.context:
        parser.error("cannot use --url and --context together")

    try:
        if args.url:
            context = build_context_from_url(args.url, args.agent)
        else:
            with open(args.context) as f:
                context = json.load(f)
            if not isinstance(context, dict):
                raise ValueError("--context must contain a JSON object")

        if args.profile:
            context = maybe_attach_profile(context)

        provider = load_provider(args.agent)
        result = run_agent(args.agent, context, provider)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    except AgentContractError as exc:
        print(json.dumps({"error": "agent contract violation", "detail": str(exc)}, indent=2))
        return 1
    except (ValueError, TypeError, RuntimeError, IOError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
