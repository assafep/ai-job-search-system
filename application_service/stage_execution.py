"""Execution ownership is explicit; submission never falls back between hosts.

The API and workstation worker must use the same backend setting. A task saves
its execution_host at admission, so later configuration changes cannot silently
move an in-progress application into another browser.
"""
import os

CHATGPT_STAGES = frozenset({'validate-bookmarked', 'submit-prepared'})
SUBMISSION_BACKENDS = frozenset({'chatgpt-native', 'local-playwright'})
SUBMISSION_BACKEND_ENV = 'AJSS_SUBMISSION_BROWSER_BACKEND'


def submission_backend():
    value = os.environ.get(SUBMISSION_BACKEND_ENV, 'chatgpt-native').strip()
    if value not in SUBMISSION_BACKENDS:
        raise ValueError('{} must be one of {}'.format(
            SUBMISSION_BACKEND_ENV, ', '.join(sorted(SUBMISSION_BACKENDS))))
    return value


def execution_host(name):
    if name == 'submit-prepared' and submission_backend() == 'local-playwright':
        return 'workstation'
    if name in CHATGPT_STAGES:
        return 'chatgpt'
    return 'workstation'


def execution_enabled(name):
    host = execution_host(name)
    if host == 'chatgpt':
        return os.environ.get('AJSS_STAGE_TASKS_ENABLED') == '1'
    return os.environ.get('AJSS_WORKSTATION_STAGES') == '1'
