# Tests

The side-effect-free contract runner makes individual agents and workloads easy
to exercise without credentials or external connections.

List available targets:

```bash
python3 -m services.test_runner list
```

The equivalent parameterized Make targets are convenient for a single test
drive:

```bash
make test-agent AGENT=interview-prep \
  FIXTURE=tests/fixtures/agents/interview-prep/input.json

make test-workload STAGE=prepare-interview \
  FIXTURE=tests/fixtures/workloads/prepare-interview.json \
  AGENT_RESULTS=tests/fixtures/workloads/prepare-interview-agent-results.json
```

Both commands remain side-effect free. `test-agent` can also receive
`OUTPUT=...` to validate a captured agent response against its schema.

Test an agent contract without invoking an LLM:

```bash
python3 -m services.test_runner agent job-discovery \
  --fixture tests/fixtures/agents/job-discovery/input.json
```

Validate a captured/model-produced output against the agent schema:

```bash
python3 -m services.test_runner agent job-discovery \
  --fixture tests/fixtures/agents/job-discovery/input.json \
  --output tests/fixtures/agents/job-discovery/output.json
```

Dry-run one workload:

```bash
python3 -m services.test_runner workload prepare-interview \
  --fixture tests/fixtures/workloads/prepare-interview.json
```

The same workload plan can be invoked directly:

```bash
python3 -m services.workload_runner prepare-interview \
  --fixture tests/fixtures/workloads/prepare-interview.json
```

To test the persistence mapping for a captured agent result, add
`--agent-results`. This still performs no writes:

```bash
python3 -m services.workload_runner prepare-interview \
  --fixture tests/fixtures/workloads/prepare-interview.json \
  --agent-results tests/fixtures/workloads/prepare-interview-agent-results.json
```

The results file must be an object keyed by agent name, for example
`{"interview-prep": {"briefing": "..."}}`.
The agent name must be one of the agents used by the selected stage.

Live persistence is opt-in and requires completed onboarding plus a running
Application Service:

```bash
python3 -m services.workload_runner prepare-interview \
  --fixture tests/fixtures/workloads/prepare-interview.json \
  --agent-results tests/fixtures/workloads/prepare-interview-agent-results.json \
  --execute
```

The direct runner shows which stateless agents would be called and which data
layer operations are proposed for the workload. It also reports approval
requirements for external actions; `ask` and `deny` never execute in dry-run
mode.

These commands do not call Google Drive, Gmail, Calendar, browser adapters, or
the Application Service. They only load fixtures, assemble a contract, and print
JSON results.

Run `make verify PYTHON=.venv/bin/python` (or use another Python 3.10+ interpreter)
for the complete local pass, including the test suite,
compilation, all four stage dry-tasks, and Compose configuration validation.

Live stage execution must call `services.runtime_config.load()` first; this
raises an onboarding error when `config/runtime.json` is absent or incomplete.
