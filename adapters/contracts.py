"""Provider-neutral event normalization contracts for external adapters."""


def _require(raw, field):
    value = raw.get(field)
    if not value:
        raise ValueError("normalized adapter input is missing " + field)
    return value


def calendar_event(raw):
    event_id = _require(raw, "event_id")
    return {
        "event_type": "calendar_event",
        "event_id": event_id,
        "payload": {
            "meeting_id": raw.get("meeting_id", event_id),
            "start": raw.get("start"),
            "end": raw.get("end"),
            "title": raw.get("title"),
            "interviewer": raw.get("interviewer"),
            "application_id": raw.get("application_id"),
        },
    }


def incoming_email(raw):
    message_id = _require(raw, "message_id")
    return {
        "event_type": "incoming_email",
        "event_id": message_id,
        "payload": {
            "message_id": message_id,
            "thread_id": raw.get("thread_id", message_id),
            "sender": raw.get("sender"),
            "subject": raw.get("subject"),
            "body": raw.get("body", ""),
            "application_id": raw.get("application_id"),
        },
    }


def job_discovery_tick(raw):
    event_id = _require(raw, "event_id")
    return {
        "event_type": "job_discovery_schedule",
        "event_id": event_id,
        "payload": {
            "source_names": raw.get("source_names", []),
            "search_terms": raw.get("search_terms", []),
            "locations": raw.get("locations", []),
            "results_per_page": raw.get("results_per_page", 20),
            "requested_at": raw.get("requested_at"),
        },
    }


def reconciliation_tick(raw):
    event_id = _require(raw, "event_id")
    return {
        "event_type": "reconciliation_schedule",
        "event_id": event_id,
        "payload": {"requested_at": raw.get("requested_at")},
    }
