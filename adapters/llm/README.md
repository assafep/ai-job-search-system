# LLM adapters

> **Shared LLM providers:** `gdtq.cli` reuses `OpenAIProvider` and `services.agent_runner` for cover letters and optional interview research. The old `AGENT_PROVIDER`, Scout and Codex runner configuration below describes compatibility stage paths. GDTQ business outcomes are validated separately using `schemas/gdtq/`.

---

## Historical / compatibility reference


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

Scout uses `coding_agent`. Set `CODING_AGENT` to the local command (`grok` or
`codex`), or persist it as `scout.coding_agent` in `config/runtime.json`.
Environment settings override runtime settings. The launcher does not choose a model unless `CODING_AGENT_MODEL` or `scout.model` is
set, and it does not send `XAI_API_KEY`. When the context contains
`read_posting_url`, an agent that can browse is asked to open that URL.
`DISCOVERY_PROVIDER=module:attribute` replaces this launcher.
