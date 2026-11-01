"""Pure helpers for the Interviews Journal document format.

Event structure
---------------
Every journal event consists of three parts, separated by blank paragraphs:

    YYYY-MM-DD <blank line>
    • Bullet 1 <blank line>
    • Bullet 2 <blank line>
    • Bullet 3

The date sits on its own line (not bulleted).  Each bullet is on its own line.
A trailing blank paragraph separates adjacent events.  Use ``event_text()`` to
produce the raw string, or ``build_event_text()`` to include the leading
separator that precedes an event appended at the end of a tab.

A status-changing event starts with one bullet, ``{source} --> {target}``.
The bullets after it are the caller's notes.
"""

import hashlib
import re
from collections import namedtuple
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


# Canonical representation of a journal event.
JournalEvent = namedtuple("JournalEvent", ["date", "bullets"])


def transition_bullet(source, target):
    """Older journals recorded a state change as one sentence."""
    return "Moved from {} to {}".format(source, target)


def transition_arrow(source, target):
    """The only sentence that prints a state change."""
    return "{} --> {}".format(source, target)


def parse_transition_arrow(bullet):
    """Return (source, target) from the one transition sentence, or None."""
    source, separator, target = str(bullet or "").partition(" --> ")
    if not separator or not source or not target or " --> " in target:
        return None
    return source, target


def with_transition_first(detail, source, target):
    """One transition bullet first, then the caller's notes."""
    line = transition_arrow(source, target)
    rest = []
    for item in detail:
        if item == line or item == transition_bullet(source, target):
            continue
        if item in {"State: {}".format(source), "Next state: {}".format(target)}:
            continue
        if parse_transition_arrow(item):
            continue
        rest.append(item)
    return [line] + rest


STATE_TABS = (
    "Bookmarked",
    "Prepared",
    "Manual Apply Required",
    "Applied",
    "Interviewing",
    "Rejected",
    "Offer",
)

OPTIONAL_STATE_TABS = ("Discovered", "Validated", "Invalidated", "Duplicate", "Dead Letters Queue")
ALL_STATES = STATE_TABS + OPTIONAL_STATE_TABS

# Completed states live in a separate document suffixed with " - Completed".
# They are still part of ALL_STATES for validation, but the repository routes
# transitions to these states to the completed document.
COMPLETED_STATES = ("Rejected", "Invalidated")

TRACKING_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}


def normalize_posting_url(value):
    """Canonicalize common tracking-only URL differences for deduplication."""
    value = (value or "").strip()
    if not value:
        return value
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    query = urlencode([
        (key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ])
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))

DATE_PATTERNS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d-%m-%Y",
)

DATE_PREFIX_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})(?:\s+|$)"
)
ATTACHED_DATE_SUFFIX_RE = re.compile(
    r"^(?P<text>.+?)(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})$"
)


def is_date_paragraph(text, is_list_item=False):
    if is_list_item:
        return False
    value = text.strip()
    return any(_try_date(value, pattern) for pattern in DATE_PATTERNS)


def date_prefix(text, is_list_item=False):
    """Return a leading date and trailing event text from legacy paragraphs."""
    if is_list_item:
        return None
    value = (text or "").strip()
    match = DATE_PREFIX_RE.match(value)
    if not match or not any(_try_date(match.group("date"), pattern) for pattern in DATE_PATTERNS):
        return None
    return match.group("date"), value[match.end():].strip()


def normalize_date(value):
    value = value.strip()
    for pattern in DATE_PATTERNS:
        parsed = _try_date(value, pattern)
        if parsed:
            return parsed.strftime("%Y-%m-%d")
    return value


def journal_timestamp(value):
    """Represent a journal's date-only value as an API date-time."""
    normalized = normalize_date(str(value))
    if re.match(r"^\d{4}-\d{2}-\d{2}$", normalized):
        return normalized + "T00:00:00+00:00"
    return str(value)


def _try_date(value, pattern):
    try:
        return datetime.strptime(value, pattern)
    except ValueError:
        return None


def parse_application_title(title):
    clean = re.sub(r"^\(Rejected\)\s*", "", title.strip())
    clean = re.sub(r"\s*\(Rejected\)\s*$", "", clean)
    parts = re.split(r"\s+/\s*|\s*/\s+", clean, maxsplit=1)
    if len(parts) == 2:
        company, role = parts
    else:
        company, role = clean, ""
    return company.strip(), role.strip()


def stable_event_id(application_tab_id, date_text, bullets, start_index):
    material = "\n".join([application_tab_id, date_text, str(start_index)] + bullets)
    return "evt_" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:20]


def parse_events(paragraphs, application_tab_id):
    events = []
    current = None
    for paragraph in paragraphs:
        text = paragraph.get("text", "").strip()
        if not text:
            continue
        is_list_item = paragraph.get("isListItem", False)
        prefix = date_prefix(text, is_list_item)
        # Repair legacy tabs where a date was accidentally formatted as part
        # of the preceding list. Only accept an exact date in that case.
        if not prefix and is_list_item and is_date_paragraph(text, False):
            prefix = date_prefix(text, False)
        if prefix:
            date_text, remainder = prefix
            if current:
                events.append(current)
            current = {
                "date": normalize_date(date_text),
                "display_date": date_text,
                "bullets": [],
                "start_index": paragraph.get("startIndex", 0),
            }
            if remainder:
                current["bullets"].append(remainder)
        elif current:
            # A previous append could have landed before the final newline of
            # a paragraph. Recover the resulting legacy shape, e.g.
            # "Added by Job Scout2026-10-13", during reads and repair.
            attached = ATTACHED_DATE_SUFFIX_RE.match(text)
            if attached and not text[:attached.start("date")].endswith(" "):
                before = attached.group("text").strip()
                date_text = attached.group("date")
                if before and any(_try_date(date_text, pattern) for pattern in DATE_PATTERNS):
                    current["bullets"].append(before)
                    events.append(current)
                    current = {
                        "date": normalize_date(date_text),
                        "display_date": date_text,
                        "bullets": [],
                        "start_index": paragraph.get("startIndex", 0),
                    }
                    continue
            current["bullets"].append(text)
    if current:
        events.append(current)
    for event in events:
        event["event_id"] = stable_event_id(
            application_tab_id,
            event["display_date"],
            event["bullets"],
            event["start_index"],
        )
    return events


def event_text(event_date, bullets):
    """Build the raw text of a single journal event (no leading separator).

    Structure::

        YYYY-MM-DD<nl><nl>bullet1<nl>bullet2<nl>...<nl><nl>

    The date sits on its own line, followed by a blank spacer, then each
    bullet on its own line, and a trailing blank paragraph that separates
    this event from the next.
    """
    if not bullets:
        raise ValueError("journal events require at least one bullet")
    return event_date + "\n\n" + "\n".join(bullets) + "\n\n"


def build_event_text(event_date, bullets, separator="\n\n"):
    """Build event text including the leading separator for appending.

    This is the single canonical way to produce event text for insertion
    into a Google Docs tab.  It returns ``separator + event_text(...)``.
    """
    if not bullets:
        raise ValueError("journal events require at least one bullet")
    return separator + event_text(event_date, bullets)
