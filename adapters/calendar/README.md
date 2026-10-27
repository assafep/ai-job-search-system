# Calendar observation

`adapters.google_events.GoogleEventSource.calendar()` is a read-only Google Calendar events adapter; its default window covers the next 30 days. In the GDTQ design, `gdtq.observers.CalendarObserver` matches plausible recruiting meetings to **Applied/Interviewing** applications and records source event IDs in the persistent index.

On a positive match, `gdtq.observers.ResearchAgent` reads the full application history via `GDTQ.application_events`, invokes an injected research provider, then saves the briefing as a nested Google Doc tab beneath the stable application tab. Reschedules are processed by event ID/version; ambiguous company matches are skipped.

Calendar reads require OAuth scopes and `AJSS_GDTQ_ENABLE_OBSERVERS=1`; they are **disabled by default**. GDTQ does not create or accept meetings on the user's behalf.

See [GDTQ integration points](../../gdtq/README.md).
