"""Small, deterministic approval-policy evaluator.

The evaluator only decides whether an action is allowed, needs human review, or
is denied. It never performs the action and never contacts an external system.
"""

import os


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_POLICY_PATH = os.path.join(ROOT, "config", "automation-policy.yaml")
VALID_DECISIONS = {"allow", "ask", "deny"}


def load_policy(path=DEFAULT_POLICY_PATH):
    policy = {}
    with open(path) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            policy[key.strip()] = value.strip()
    return policy


def decision(action, policy=None):
    policy = load_policy() if policy is None else policy
    value = policy.get(action, "ask")
    if value not in VALID_DECISIONS:
        raise ValueError("invalid approval decision for {}: {}".format(action, value))
    return value


def evaluate(actions, policy=None):
    return [{"action": action, "decision": decision(action, policy)} for action in actions]

