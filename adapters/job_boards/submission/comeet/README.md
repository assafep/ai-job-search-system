# Comeet submission

`adapter.py:submit` is the framework callback configured by
`ajss.submission.wiring.configured_strategies`. It validates the destination,
creates a temporary PDF and launches `browser.cjs` once. `browser.cjs` is an
internal subprocess worker, not a separate operator tool.

Requires explicit `AJSS_SUBMISSION_BACKEND=comeet`, Node, Playwright and Chrome
(optional `BROWSER_EXECUTABLE`). The default Python image has no browser setup.
The worker supports known Comeet controls, uses the combined PDF in both upload
slots, and stops for unknown required controls or verification challenges.

Live activation still requires nonproduction browser validation and permission.
See [operations and evidence limits](../../../../ajss/SUBMISSION.md).
