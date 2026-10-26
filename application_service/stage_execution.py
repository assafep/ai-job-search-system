"""Execution ownership is independent of activation; no implicit local fallback."""
import os

CHATGPT_STAGES = frozenset({'validate-bookmarked', 'submit-prepared'})


def execution_host(name):
    if name in CHATGPT_STAGES:
        return 'chatgpt'
    return 'workstation'


def execution_enabled(name):
    host = execution_host(name)
    if host == 'chatgpt':
        return os.environ.get('AJSS_STAGE_TASKS_ENABLED') == '1'
    return os.environ.get('AJSS_WORKSTATION_STAGES') == '1'
