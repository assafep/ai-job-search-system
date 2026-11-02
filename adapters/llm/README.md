# OpenAI model adapter

`adapters.llm.openai_provider.OpenAIProvider` posts to an OpenAI-compatible `/chat/completions` endpoint. `adapters.llm.providers` maps `AJSS_LLM_PROVIDER` to Grok, OpenAI, Anthropic, Gemini, or a custom base URL, and reads `AJSS_LLM_API_KEY` plus optional `AJSS_LLM_MODEL`. When `AJSS_LLM_PROVIDER` is unset, `OPENAI_API_KEY` selects OpenAI and `XAI_API_KEY` selects Grok. Prompt and output validation remain independent from the provider implementation; application state is never mutated by the model.
