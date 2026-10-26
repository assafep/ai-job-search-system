"""Side-effect-free workload and agent contract test runner.

This runner deliberately does not call LLMs or external adapters. It assembles
the context an agent would receive, validates a supplied structured output, and
prints a dry-run workload plan. That makes individual pieces easy to test in CI
or by hand without touching Google Drive or changing application state.
"""

import argparse
import json
import os
import sys


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AGENTS = {
    "applicant-profile": "agents/applicant-profile",
    "read-posting": "agents/read-posting",
    "validate": "agents/validate",
    "application": "agents/application",
    "interview-prep": "agents/interview-prep",
    "correspondence": "agents/correspondence",
}


def read_json(path):
    with open(path) as handle:
        return json.load(handle)


def resolve(path):
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


# Compatibility for existing callers of the dry-run runner. Definition loading
# belongs to the stage layer rather than the test infrastructure.
from .stage_definitions import discover_stages, parse_stage, STAGES

parse_workflow = parse_stage
discover_workflows = discover_stages


WORKFLOWS = STAGES


def validate_output(value, schema, path="output"):
    """Validate the JSON-schema subset used by agent contracts recursively."""
    if "oneOf" in schema:
        branch_errors = [validate_output(value, branch, path) for branch in schema["oneOf"]]
        if any(not errors for errors in branch_errors):
            return []
        return ["{} must match one of the declared output shapes".format(path)]
    errors = []
    wanted = schema.get("type")
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }
    wanted_types = wanted if isinstance(wanted, list) else [wanted]
    if wanted_types and not any(matches.get(item, True) for item in wanted_types):
        errors.append("{} must be one of {}".format(path, wanted_types))
        return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append("{} must be one of {}".format(path, schema["enum"]))
    if "object" in wanted_types:
        for field in schema.get("required", []):
            if field not in value:
                errors.append("missing required field: {}".format(field))
        for field, definition in schema.get("properties", {}).items():
            if field in value:
                errors.extend(validate_output(value[field], definition, path + "." + field))
    elif "array" in wanted_types and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(validate_output(item, schema["items"], "{}[{}]".format(path, index)))
    return errors


def agent_test(agent_name, fixture_path, output_path=None):
    if agent_name not in AGENTS:
        raise ValueError("unknown agent: " + agent_name)
    directory = AGENTS[agent_name]
    base = resolve(directory)
    fixture = read_json(resolve(fixture_path))
    schema = read_json(os.path.join(base, "output.schema.json"))
    result = {
        "mode": "agent-contract",
        "agent": agent_name,
        "prompt_file": directory + "/prompt.md",
        "context": fixture,
        "output_schema": directory + "/output.schema.json",
    }
    if output_path:
        output = read_json(resolve(output_path))
        errors = validate_output(output, schema)
        result["output"] = output
        result["valid"] = not errors
        result["errors"] = errors
        if errors:
            return 1, result
    else:
        result["valid"] = None
        result["note"] = "No LLM was called; supply --output to validate an agent result."
    return 0, result


def workload_test(workflow_name, fixture_path):
    fixture = read_json(resolve(fixture_path))
    from .workload_runner import build_plan
    result = build_plan(workflow_name, fixture)
    result["fixture"] = fixture
    result["valid"] = bool(result.get("outcomes"))
    return 0 if result["valid"] else 1, result


def list_items():
    return {
        "agents": sorted(AGENTS),
        "workflows": sorted(WORKFLOWS),
        "examples": {
            "agent": "python3 -m services.test_runner agent read-posting --fixture tests/fixtures/agents/read-posting/input.json",
            "agent_with_output": "python3 -m services.test_runner agent read-posting --fixture tests/fixtures/agents/read-posting/input.json --output tests/fixtures/agents/read-posting/output.json",
            "workload": "python3 -m services.test_runner workload prepare-interview --fixture tests/fixtures/workloads/prepare-interview.json",
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run side-effect-free agent/workload contract tests")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list")
    agent = subparsers.add_parser("agent")
    agent.add_argument("name", choices=sorted(AGENTS))
    agent.add_argument("--fixture", required=True)
    agent.add_argument("--output")

    workload = subparsers.add_parser("workload")
    workload.add_argument("name", choices=sorted(WORKFLOWS))
    workload.add_argument("--fixture", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            code, result = 0, list_items()
        elif args.command == "agent":
            code, result = agent_test(args.name, args.fixture, args.output)
        elif args.command == "workload":
            code, result = workload_test(args.name, args.fixture)
        else:
            parser.print_help()
            return 2
    except (IOError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
