# Calendar adapter

Converts new or changed interview meetings into interview-preparation events.

`adapters/google_events.py` reads the primary calendar's next 30 days.
The runtime matches interview events to existing active applications; ambiguous
matches and non-interview events are skipped. Event ID plus update revision
makes retries idempotent while allowing rescheduling updates. No Calendar
writes are performed. `calendar.events.readonly` authorization is required.
