"""Versioned deterministic validation and exhaustive outcome-to-state resolution."""
from __future__ import annotations
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from .contracts import Decision

ROOT = Path(__file__).resolve().parents[1]
class RoutingError(ValueError):
    pass

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

class OutcomeValidator:
    def __init__(self, schema_dir=ROOT / "schemas" / "gdtq"):
        self.schema_dir = Path(schema_dir)

    def validate(self, agent: str, payload: dict) -> dict:
        path = self.schema_dir / (agent.replace("-", "_") + ".schema.json")
        if not path.is_file():
            raise RoutingError("No registered schema for agent: " + agent)
        schema = read_json(path)
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(map(str, e.absolute_path)))
        if errors:
            raise RoutingError("; ".join("/".join(map(str, e.path)) + ": " + e.message for e in errors))
        if payload["agent"] != agent:
            raise RoutingError("Agent identity mismatch")
        return payload

class StateTransitionEngine:
    def __init__(self, config_path=ROOT / "config" / "gdtq-routing.json", schema_dir=ROOT / "schemas" / "gdtq"):
        self.config = read_json(config_path)
        self.validator = OutcomeValidator(schema_dir)
        self.version = str(self.config["version"])
        self.transitions = {k: set(v) for k, v in self.config["allowed_transitions"].items()}
        self.routes = self.config["routes"]
        self.terminal = set(self.config["terminal_states"])
        self._check_configuration()

    def _check_configuration(self):
        states = set(self.transitions)
        if not self.terminal <= states | {"Failed", "Expired"}:
            raise RoutingError("Unknown terminal states")
        for src, dests in self.transitions.items():
            if set(dests) - states:
                raise RoutingError("Unknown destination from " + src)
        for agent, rule in self.routes.items():
            schema = read_json(self.validator.schema_dir / (agent + ".schema.json"))
            codes = set(schema["properties"]["code"]["enum"])
            configured = set(rule["outcomes"])
            if codes != configured:
                raise RoutingError(f"Non-exhaustive mapping for {agent}: missing={codes-configured}, extra={configured-codes}")
            for src in rule["source_states"]:
                if src not in states:
                    raise RoutingError("Unknown source state: " + src)
                for code, dst in rule["outcomes"].items():
                    if dst not in states and dst not in {"Failed", "Expired"}:
                        raise RoutingError("Unknown destination: " + dst)
                    if dst not in self.transitions[src] and dst != src and dst not in {"Failed", "Expired"}:
                        raise RoutingError(f"Illegal route {agent} / {code}: {src} -> {dst}")

    def resolve(self, source_state: str, payload: dict) -> Decision:
        agent = payload.get("agent", "")
        self.validator.validate(agent, payload)
        rule = self.routes.get(agent)
        if not rule or source_state not in rule["source_states"]:
            raise RoutingError(f"Agent {agent} is not registered for state {source_state}")
        if payload["execution_status"] == "RETRYABLE_FAILURE":
            return Decision(source_state, source_state, False, "RETRYABLE_FAILURE", self.version)
        if payload["execution_status"] == "PERMANENT_FAILURE":
            # Before external submission, these are durable failures; otherwise
            # submissions with uncertain results must never be blindly repeated.
            target = "Failed" if agent == "bookmark_discovered" else "Dead Letters Queue"
            if target not in self.transitions[source_state] and target not in {"Failed", "Expired"}:
                raise RoutingError("Permanent failure cannot be routed from " + source_state)
            return Decision(source_state, target, target in self.terminal, "PERMANENT_FAILURE", self.version)
        target = rule["outcomes"][payload["code"]]
        if target != source_state and target not in self.transitions[source_state] and target not in {"Failed", "Expired"}:
            raise RoutingError(f"Invalid transition: {source_state} -> {target}")
        return Decision(source_state, target, target in self.terminal, payload["code"], self.version)

    def quarantine(self, source_state: str) -> Decision:
        """Administrative recovery; intentionally independent of invalid agent output."""
        if source_state in self.terminal:
            raise RoutingError("Cannot quarantine completed item")
        return Decision(source_state, "Dead Letters Queue", False, "INVALID_OUTCOME", self.version)
