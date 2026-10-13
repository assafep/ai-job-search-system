# Browser adapter

Optional boundary for sites that require browser interaction. It must be
invoked by a workload runtime, never by a stateless agent.

Every unsuccessful auto-apply attempt moves postings from `Ready for Auto-Apply`
to `Manual Apply Required`, with a reason recorded by the auto-apply runtime.
Distinguish human verification, missing answers, unsupported destinations,
preparation errors, and ambiguous receipts accurately. Never bypass checks or
blindly retry an ambiguous submission. Ready for Auto-Apply queue membership
authorizes submission without a separate approval pause. The manual queue is
not consumed automatically.

`chrome_session.cjs` attaches to the user's already-running normal Chrome via
Chrome DevTools MCP's `--autoConnect` consent flow. Enable remote debugging at
`chrome://inspect/#remote-debugging`, then approve Chrome's connection dialog.
It does not copy/export cookies, restart Chrome, or close the browser when
disconnected. Its JSON-lines interface accepts MCP tool names/arguments.
`trial_session.cjs` instead uses isolated per-site profiles; these are different
from the user's normal signed-in Chrome session. Read the relevant playbook
before invoking learned recipes.
