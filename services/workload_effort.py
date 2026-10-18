"""Validated reasoning presets, reloaded before each workload invocation."""

import json
import os

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'config', 'workload-effort.json')
LEVELS = {'low', 'medium', 'high', 'xhigh'}
WORKFLOWS = {'discover-and-bookmark', 'prepare-bookmarked',
             'prepare-interview', 'handle-incoming-email', 'reconcile', 'submit-ready'}


def effort_for_workflow(workflow, path=None):
    path = path or os.environ.get('WORKLOAD_EFFORT_CONFIG_PATH') or DEFAULT_PATH
    with open(path) as handle:
        settings = json.load(handle)
    if not isinstance(settings, dict) or settings.get('default') not in LEVELS:
        raise ValueError('workload effort default must be low, medium, high, or xhigh')
    presets = settings.get('workflows', {})
    if not isinstance(presets, dict):
        raise ValueError('workload effort workflows must be an object')
    for name, value in presets.items():
        if name not in WORKFLOWS:
            raise ValueError('unknown workload effort workflow: ' + name)
        if value not in LEVELS:
            raise ValueError('invalid reasoning effort for ' + name)
    if workflow not in WORKFLOWS:
        raise ValueError('unknown workflow: ' + workflow)
    return presets.get(workflow, settings['default'])
