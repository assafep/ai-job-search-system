# Calendly / scheduling link

Calendly is an applicant-provided URL in the existing `config/runtime.json` scheduling configuration. GDTQ does not operate a Calendly API or create appointments automatically.

The correspondence helper `gdtq.stages.reply_to_interview_request` can include the configured link and contact phone in a recruiter response. By default `gdtq.gmail.GmailActions` creates a **draft**; real sending requires an explicit `AJSS_GMAIL_ALLOW_SEND=1` gate and the authorized Gmail OAuth scope.

See [Gmail](../gmail/README.md) and [GDTQ](../../gdtq/README.md).
