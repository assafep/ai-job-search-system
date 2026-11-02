# Submission adapters

`contracts.py` is used by the board framework, GDTQ processing and provider
adapters to validate outcomes. `__init__.py` preserves existing public imports.

`comeet/adapter.py` is selected by `ajss.submission.wiring` and invoked through
the framework callback. It alone launches `comeet/browser.cjs`.

See [Comeet requirements](comeet/README.md) and the
[complete caller map](../../../ajss/SUBMISSION.md).
