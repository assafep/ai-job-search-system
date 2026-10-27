# Adapters

Only active adapter code is retained. `google/` owns Google Docs/Drive/OAuth and journal parsing; `job_boards/` owns ATS search and content retrieval; `google_events.py` polls Gmail and Calendar; `llm/openai_provider.py` is the JSON model provider. No Application Service layer exists.

I/O adapters do not choose the next queue state; they return data/errors to deterministic GDTQ stages. Outgoing Gmail messages and final application submissions require explicit configuration and authorization.
