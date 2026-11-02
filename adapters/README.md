# Adapters

Only active adapter code is retained. `google/` owns Google Docs/Drive/OAuth and journal parsing; `job_boards/` owns ATS search and content retrieval; `google_events.py` polls Gmail and Calendar; `llm/openai_provider.py` is the OpenAI-compatible JSON model provider. The provider name or base URL is `AJSS_LLM_PROVIDER`. No Application Service layer exists.

AJSS I/O adapters do not choose the next queue state; they return data/errors to deterministic GDTQ stages. Outgoing Gmail messages and final application submissions require explicit configuration and authorization.
