# Gmail adapter

Converts incoming messages into correspondence workload events and sends only
approved responses.

`adapters/google_events.py` implements read-only recruitment-message polling.
`services/external_event_polling.py` matches only existing Applied/Interviewing
applications and dispatches stable message IDs. It does not send messages;
response sending is not implemented. `gmail.readonly` authorization is required.
