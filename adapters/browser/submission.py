"""External submission boundary; adapters must return observed form evidence."""

import importlib
import os


def adapter_from_environment():
    spec = os.environ.get('JOB_SUBMISSION_ADAPTER')
    if not spec:
        from .comeet import submit
        return submit
    module, attribute = spec.split(':', 1)
    adapter = getattr(importlib.import_module(module), attribute)
    if not callable(adapter):
        raise ValueError('JOB_SUBMISSION_ADAPTER must identify a callable')
    return adapter


def unavailable(context):
    return {
        'status': 'Blocked',
        'failure_kind': 'system_error',
        'confirmation_text': None,
        'application_reference': None,
        'blockers': ['No browser submission adapter is configured. Set JOB_SUBMISSION_ADAPTER=module:callable to a supported browser integration.'],
    }
