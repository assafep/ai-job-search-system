"""Pure prompt runner with JSON Schema validation; no legacy stage-task imports."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator
ROOT=Path(__file__).resolve().parents[1]
class AgentContractError(ValueError): pass
def run(agent_name,context,provider):
    if agent_name not in {"application","interview-prep","correspondence","read-posting","validate","applicant-profile"}:
        raise ValueError("Unknown AI agent: "+agent_name)
    if not isinstance(context,dict):raise ValueError("Agent context must be an object")
    folder=ROOT/"agents"/agent_name
    prompt=(folder/"prompt.md").read_text(encoding="utf-8")
    schema=json.loads((folder/"output.schema.json").read_text(encoding="utf-8"))
    if callable(getattr(provider,"generate_with_schema",None)):
        selected=schema
        if agent_name=="application" and "oneOf" in schema:
            selected=schema["oneOf"][1 if "discovered_candidates" in context else 0]
        output=provider.generate_with_schema(prompt,context,selected)
    else:
        fn=provider if callable(provider) else getattr(provider,"generate",None)
        if not callable(fn):raise TypeError("Provider must be callable or implement generate")
        output=fn(prompt,context)
    if isinstance(output,str):
        try:output=json.loads(output)
        except ValueError as exc:raise AgentContractError("Agent returned invalid JSON") from exc
    failures=list(Draft202012Validator(schema).iter_errors(output))
    if failures:raise AgentContractError("; ".join(error.message for error in failures))
    return output
