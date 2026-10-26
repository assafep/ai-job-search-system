"""Validated reasoning presets, reloaded before each workload invocation."""

import json
import os

from .stage_definitions import discover_stages

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'config', 'workload-effort.json')
LEVELS = {'low', 'medium', 'high', 'xhigh'}
STAGES = set(discover_stages().keys())


def effort_for_stage(stage, path=None):
    path = path or os.environ.get('WORKLOAD_EFFORT_CONFIG_PATH') or DEFAULT_PATH
    with open(path) as handle:
        settings = json.load(handle)
    if not isinstance(settings, dict) or settings.get('default') not in LEVELS:
        raise ValueError('workload effort default must be low, medium, high, or xhigh')
    if set(settings) - {'default', 'stages'}:
        raise ValueError('Unknown workload effort configuration fields')
    presets = settings.get('stages', {})
    if not isinstance(presets, dict):
        raise ValueError('workload effort stages must be an object')
    for name, value in presets.items():
        if name not in STAGES:
            raise ValueError('unknown workload effort stage: ' + name)
        if value not in LEVELS:
            raise ValueError('invalid reasoning effort for ' + name)
    if stage not in STAGES:
        raise ValueError('unknown stage: ' + stage)
    return presets.get(stage, settings['default'])
