"""Load declarative processing stages independently of worker execution.

Stages declare triggers, input states, agents and outcome routing. Workers
execute tasks for those stages; a workflow connects stages across the lifecycle.
"""

import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def resolve(path):
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def parse_stage(path):
    """Parse a stage YAML file returning name, trigger, agent, input_states, external_actions, outcomes, and side_effects.

    Uses a minimal line-based parser to avoid a hard PyYAML dependency.
    The YAML format is simple enough that this approach is robust.
    """
    name = None
    trigger = {}
    agent = None
    input_states = []
    external_actions = []
    outcomes = {}
    side_effects = None
    current_section = None

    with open(resolve(path)) as handle:
        for line in handle:
            stripped = line.rstrip()
            if not stripped or stripped.startswith('#'):
                continue
            if not line.startswith(' ') and not line.startswith('\t'):
                if stripped.startswith('name:'):
                    name = stripped.split(':', 1)[1].strip()
                    current_section = 'name'
                elif stripped.startswith('trigger:'):
                    current_section = 'trigger'
                elif stripped.startswith('agent:'):
                    value = stripped.split(':', 1)[1].strip()
                    agent = None if value == 'null' else value
                    current_section = 'agent'
                elif stripped.startswith('input_states:'):
                    inline = stripped.split(':', 1)[1].strip()
                    if inline == '[]':
                        current_section = None
                    else:
                        current_section = 'input_states'
                elif stripped.startswith('external_actions:'):
                    inline = stripped.split(':', 1)[1].strip()
                    if inline == '[]':
                        current_section = None
                    else:
                        current_section = 'external_actions'
                elif stripped.startswith('outcomes:'):
                    current_section = 'outcomes'
                elif stripped.startswith('side_effects:'):
                    side_effects = stripped.split(':', 1)[1].strip()
                    current_section = 'side_effects'
                else:
                    current_section = None
                continue
            if current_section == 'trigger':
                match = re.match(r'\s+(\w[\w_]*):\s*(.*)', stripped)
                if match:
                    value = match.group(2).strip()
                    trigger[match.group(1)] = None if value == 'null' else value
            elif current_section in ('input_states', 'external_actions'):
                match = re.match(r'\s+-\s+(.+)', stripped)
                if match:
                    if current_section == 'input_states':
                        input_states.append(match.group(1).strip())
                    elif current_section == 'external_actions':
                        external_actions.append(match.group(1).strip())
            elif current_section == 'outcomes':
                match = re.match(r'\s+([A-Za-z0-9_]+):\s*(.*)', stripped)
                if match:
                    value = match.group(2).strip()
                    outcomes[match.group(1)] = None if value == 'null' else value
    return {
        'name': name,
        'trigger': trigger,
        'agent': agent,
        'input_states': input_states,
        'external_actions': external_actions,
        'outcomes': outcomes,
        'side_effects': side_effects,
        'path': path,
    }


def discover_stages():
    """Auto-discover stage definitions from the stages/ directory.

    Returns a dict mapping stage name to relative YAML path, sorted by filename.
    """
    stages_dir = os.path.join(ROOT, "stages")
    result = {}
    if os.path.isdir(stages_dir):
        for filename in sorted(os.listdir(stages_dir)):
            if filename.endswith(".yaml"):
                path = "stages/" + filename
                definition = parse_stage(path)
                if definition.get("name"):
                    result[definition["name"]] = path
    return result


STAGES = discover_stages()
