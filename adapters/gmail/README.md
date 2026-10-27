# Gmail correspondence

The existing `adapters.google_events.GoogleEventSource.gmail()` reads recruitment messages using OAuth and returns normalized IDs, sender, subject, body and thread information.

GDTQ's `EmailCorrespondenceObserver` matches messages to active applications, checkpoints message IDs to prevent replay and classifies rejection, offer, interview follow-up or ambiguous messages. An observer update uses `WorkloadOrchestrator.handle_external` to claim the **specific application** first.

Outgoing messages are handled by `gdtq.gmail.GmailActions`. **Draft-only is the default.** Sending requires an explicit `allow_send` permission, valid recipient and appropriate Gmail scopes. Do not infer permission from queue membership. Ambiguous mail must not trigger an automatic reply or terminal transition.

Hourly observation is opt-in with `AJSS_GDTQ_ENABLE_OBSERVERS=1`; configure recipients and credentials before enabling. See [GDTQ](../../gdtq/README.md).
