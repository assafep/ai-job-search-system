"""Provider-neutral execution boundary for stateless stage agents.

The runner owns contract loading and output validation. The supplied provider
is only an invocation function; it receives prompt text and serialized
context, and the runner never gives it persistence or external-system clients.
"""

import json

from .test_runner import AGENTS, read_json, resolve, validate_output


class AgentContractError(ValueError):
    """Raised when an agent returns a result outside its declared contract."""


def contract(agent_name):
    if agent_name not in AGENTS:
        raise ValueError("unknown agent: " + agent_name)
    directory = resolve(AGENTS[agent_name])
    with open(directory + "/prompt.md") as handle:
        prompt = handle.read()
    schema = read_json(directory + "/output.schema.json")
    return {"agent": agent_name, "prompt": prompt, "schema": schema}


def run(agent_name, context, provider):
    """Invoke and validate one stateless agent through an injected provider.

    ``provider`` must have the shape ``provider(prompt, context)`` and may
    return a decoded object or a JSON string. It cannot perform persistence
    through this interface because only prompt/context are supplied.
    """
    if (
        not callable(provider)
        and not callable(getattr(provider, "generate", None))
        and not callable(getattr(provider, "generate_with_schema", None))
    ):
        raise TypeError("provider must be callable or expose generate(prompt, context)")
    if not isinstance(context, dict):
        raise ValueError("agent context must be an object")
    selected = contract(agent_name)
    if hasattr(provider, "generate_with_schema"):
        output_schema = selected["schema"]
        # Codex's structured-output endpoint requires one concrete object
        # schema. The application contract intentionally has two valid shapes;
        # choose the batch shape when discovery supplied multiple postings and
        # the single-application shape otherwise. Validation still uses the
        # original oneOf contract below.
        if agent_name == "application" and "oneOf" in output_schema:
            branch = 1 if context.get("discovered_candidates") is not None else 0
            output_schema = output_schema["oneOf"][branch]
        result = provider.generate_with_schema(
            selected["prompt"], context, output_schema
        )
    else:
        invoke = provider if callable(provider) else provider.generate
        result = invoke(selected["prompt"], context)
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError as exc:
            raise AgentContractError("agent returned invalid JSON: {}".format(exc))
    errors = validate_output(result, selected["schema"])
    if errors:
        raise AgentContractError("invalid {} result: {}".format(agent_name, "; ".join(errors)))
    return result
