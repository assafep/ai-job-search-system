# Adapters

Adapters translate external systems into workload inputs and approved side
effects. They are not used directly by stateless agents.

`contracts.py` defines the provider-neutral normalized event shapes. Concrete
Gmail, Calendar, job-board, or scheduler adapters should translate into these
shapes and then call `services.event_dispatcher.dispatch`.
They may instead pass the complete normalized object to
`services.event_dispatcher.dispatch_normalized`.

Concrete Gmail, Calendar, job-board, browser, and LLM-provider clients remain
deployment-specific integrations. They may read from or write to their
assigned external system, but must not mutate application state directly or
give credentials or clients to agents. The persistence API remains the only
owner of application-state and Drive mutations.
