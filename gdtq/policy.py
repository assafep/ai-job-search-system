"""Versioned and exhaustive mapping from validated agent outcomes to queue states."""
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
        self.schema_dir=Path(schema_dir)

    def validate(self, agent, payload):
        path=self.schema_dir / (agent.replace("-", "_")+".schema.json")
        if not path.is_file():
            raise RoutingError("Unknown agent schema: "+agent)
        validator=Draft202012Validator(read_json(path))
        errors=sorted(validator.iter_errors(payload),key=lambda e:list(map(str,e.absolute_path)))
        if errors:
            raise RoutingError("; ".join("/".join(map(str,e.absolute_path))+": "+e.message for e in errors))
        if payload["agent"] != agent:
            raise RoutingError("Agent identity mismatch")
        return payload

class StateTransitionEngine:
    def __init__(self, config_path=ROOT / "config" / "gdtq-routing.json",schema_dir=ROOT / "schemas" / "gdtq"):
        self.config=read_json(config_path)
        self.validator=OutcomeValidator(schema_dir)
        self.version=str(self.config["version"])
        self.transitions={k:set(v) for k,v in self.config["allowed_transitions"].items()}
        self.routes=self.config["routes"]
        self.terminal=set(self.config["terminal_states"])
        self._check_configuration()

    def _check_configuration(self):
        states=set(self.transitions)
        if not self.terminal <= states | {"Failed","Expired","Ghosted"}:
            raise RoutingError("Unknown terminal states")
        for src,dests in self.transitions.items():
            if set(dests)-states: raise RoutingError("Unknown destination in "+src)
        for agent,rule in self.routes.items():
            schema=read_json(self.validator.schema_dir / (agent+".schema.json"))
            codes=set(schema["properties"]["code"]["enum"])
            mapped=set(rule["outcomes"])
            if codes!=mapped:
                raise RoutingError(f"Non-exhaustive mapping for {agent}: missing={codes-mapped}, extra={mapped-codes}")
            for src in rule["source_states"]:
                if src not in states: raise RoutingError("Unknown source state "+src)
                for code,dest in rule["outcomes"].items():
                    target=src if dest=="@same" else dest
                    if target not in states:
                        raise RoutingError("Unknown route target "+target)
                    if target!=src and target not in self.transitions[src]:
                        raise RoutingError(f"Illegal route {agent}/{code}: {src} -> {target}")

    def resolve(self, source_state,payload):
        agent=payload.get("agent","")
        self.validator.validate(agent,payload)
        rule=self.routes.get(agent)
        if not rule or source_state not in rule["source_states"]:
            raise RoutingError(f"Agent {agent} cannot process {source_state}")
        status=payload["execution_status"]
        if status=="RETRYABLE_FAILURE":
            return Decision(source_state,source_state,False,status,self.version)
        if status=="PERMANENT_FAILURE":
            target="Failed" if agent=="bookmark_discovered" else "Dead Letters Queue"
            if target!="Failed" and target not in self.transitions[source_state]:
                raise RoutingError("No valid permanent failure path for "+source_state)
            return Decision(source_state,target,target in self.terminal,status,self.version)
        dest=rule["outcomes"][payload["code"]]
        target=source_state if dest=="@same" else dest
        if target!=source_state and target not in self.transitions[source_state]:
            raise RoutingError("Illegal transition "+source_state+" -> "+target)
        return Decision(source_state,target,target in self.terminal,payload["code"],self.version)

    def quarantine(self,source_state):
        if source_state in self.terminal: raise RoutingError("Cannot quarantine terminal item")
        return Decision(source_state,"Dead Letters Queue",False,"INVALID_OUTCOME",self.version)
