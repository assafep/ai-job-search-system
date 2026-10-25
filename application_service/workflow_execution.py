"""Workflow execution ownership. The two browser workflows never use a worker."""
CHATGPT_WORKFLOWS = frozenset({'validate-bookmarked', 'submit-prepared'})


def execution_host(name):
    if name in CHATGPT_WORKFLOWS:
        return 'chatgpt'
    # Cloud activation is explicit; do not advertise Vercel before it is ready.
    import os
    return 'vercel' if os.environ.get('AJSS_VERCEL_WORKFLOWS_ENABLED') == '1' else 'workstation'
