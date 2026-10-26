"""Execution ownership is independent of activation; no implicit local fallback."""
import os

CHATGPT_WORKFLOWS = frozenset({'validate-bookmarked', 'submit-prepared'})


def execution_host(name):
    if name in CHATGPT_WORKFLOWS:
        return 'chatgpt'
    if os.environ.get('AJSS_WORKSTATION_WORKFLOWS') == '1':
        return 'workstation'
    return 'workstation'


def execution_enabled(name):
    host = execution_host(name)
    if host == 'chatgpt':
        return os.environ.get('AJSS_WORKFLOW_RUNS_ENABLED') == '1'
    return os.environ.get('AJSS_WORKSTATION_WORKFLOWS') == '1'
