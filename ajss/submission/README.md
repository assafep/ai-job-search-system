# Submission workflow

- `wiring.py`: called by `ajss.cli.main` through this package; creates strategies
  that load the prepared PDF/profile and call the board framework.
- `processing.py`: called by `ajss.worker.handlers` for the Prepared state; owns durable
  attempts and returns typed outcomes. The orchestrator commits state changes.
- `__init__.py`: stable public imports.

Call flow: CLI configures a strategy → handlers call processing → processing
calls the strategy → board framework → provider adapter → browser worker.

See [submission ownership and operations](../SUBMISSION.md) for the complete
caller table, activation requirements, recovery rules and PR provenance.
