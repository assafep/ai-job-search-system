# OpenAI model adapter

`adapters.llm.openai_provider.OpenAIProvider` exposes JSON output generation for the pure `gdtq.agent_runner` boundary. Configure `OPENAI_API_KEY` and optional `OPENAI_MODEL`. Prompt and output validation remain independent from the provider implementation; application state is never mutated by the model.
