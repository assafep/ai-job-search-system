from persistence_api.journal_format import (
    STATE_TABS,
    event_text,
    journal_timestamp,
    normalize_date,
    parse_application_title,
    parse_events,
)
from persistence_api.journal_format import normalize_posting_url


def test_state_tabs_are_canonical():
    assert STATE_TABS == (
        "Bookmarked", "Ready for Auto-Apply", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"
    )


def test_existing_date_formats_are_normalized():
    assert normalize_date("12.10.2026") == "2026-10-12"
    assert normalize_date("22/09/2026") == "2026-09-22"
    assert normalize_date("2026-10-12") == "2026-10-12"
    assert journal_timestamp("12.10.2026") == "2026-10-12T00:00:00+00:00"


def test_posting_urls_drop_tracking_variants_for_deduplication():
    assert normalize_posting_url(
        "HTTPS://Example.TEST/jobs/backend/?utm_source=mail&ref=campaign#section"
    ) == "https://example.test/jobs/backend?ref=campaign"


def test_application_title_and_event_shape_match_journal():
    assert parse_application_title("(Rejected) SimilarWeb / Senior Backend Engineer") == (
        "SimilarWeb", "Senior Backend Engineer"
    )
    assert parse_application_title("Lemonade (Rejected)") == ("Lemonade", "")
    events = parse_events([
        {"text": "22/09/2026", "isListItem": False, "startIndex": 1},
        {"text": "Applied through https://example.test/job", "isListItem": True, "startIndex": 12},
        {"text": "05/10/2026", "isListItem": False, "startIndex": 60},
        {"text": "Phone call with recruiter", "isListItem": True, "startIndex": 72},
    ], "tab-1")
    assert events[0]["date"] == "2026-09-22"
    assert events[0]["bullets"] == ["Applied through https://example.test/job"]
    assert event_text("2026-10-12", ["Added by workflow"])


def test_parse_events_supports_legacy_inline_date_and_event_text():
    events = parse_events([
        {"text": "26.07.2026 Applied via LinkedIn & Greenhouse", "isListItem": False, "startIndex": 1},
        {"text": "Next note", "isListItem": False, "startIndex": 50},
    ], "app-1")

    assert events[0]["date"] == "2026-07-26"
    assert events[0]["bullets"] == ["Applied via LinkedIn & Greenhouse", "Next note"]


def test_parse_events_recovers_date_attached_to_previous_bullet():
    events = parse_events([
        {"text": "2026-10-12", "isListItem": False, "startIndex": 1},
        {"text": "Added by Job Scout2026-10-13", "isListItem": True, "startIndex": 12},
        {"text": "Moved from Bookmarked to Ready for Auto-Apply", "isListItem": True, "startIndex": 50},
    ], "app-1")

    assert events[0]["bullets"] == ["Added by Job Scout"]
    assert events[1]["date"] == "2026-10-13"
    assert events[1]["bullets"] == ["Moved from Bookmarked to Ready for Auto-Apply"]


def test_event_text_separates_events_without_an_empty_bullet():
    assert event_text("2026-10-12", ["Added by workflow"]) == (
        "2026-10-12\n\nAdded by workflow\n\n"
    )
