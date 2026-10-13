# LLM adapters

Provider-specific wrappers should implement the callable boundary expected by
`services.agent_runner.run`: receive prompt text and a serialized context,
return a decoded object or JSON string, and perform no persistence or external
side effects. The runner validates structured outputs against the schemas in
`agents/` before a workload can execute.

Adapters may also implement the `LLMProvider` object contract in
[`contracts.py`](contracts.py) with a `generate(prompt, context)` method.

The included `openai_provider` uses the OpenAI Responses API and returns
decoded JSON. Configure `OPENAI_API_KEY`, optionally set `OPENAI_MODEL`, and
select it with `AGENT_PROVIDER=adapters.llm.openai_provider:provider`.

For a single-user local workspace, `codex_provider` runs `codex exec` with the
agent's output schema and uses the existing Codex CLI login. Configure
`AGENT_PROVIDER=adapters.llm.codex_provider:provider`; no API key is required.
