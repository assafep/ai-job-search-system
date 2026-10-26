"""Google Docs-backed persistence for the Interviews Journal format.

The journal is intentionally human-readable. Top-level state tabs contain
application tabs; application tabs contain dated paragraphs followed by list
items. Nested research tabs remain children of their application tab and are
not treated as separate applications.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from functools import wraps
from time import monotonic
from datetime import date

from .journal_format import (
    STATE_TABS,
    ALL_STATES,
    OPTIONAL_STATE_TABS,
    build_event_text,
    is_date_paragraph,
    normalize_posting_url,
    parse_application_title,
    parse_events,
    journal_timestamp,
    with_transition_first,
)
from .google_drive_artifacts import GoogleDriveArtifactStore
from .file_paths import normalize_file_path
from .google_auth import token_from_environment, access_token_value
from .models import Application, Artifact, Event, now
from .profile import validate_profile
from .repository import Repository
from .state_machine import validate_transition


URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
ARTIFACT_RE = re.compile(r"^Artifact:\s*(.*?)\s*(?:\(Drive file:\s*([^\)]+)\))?$", re.IGNORECASE)
LEGACY_ARTIFACT_RE = re.compile(
    r"^(Application PDF|Applied Resume|Applied Cover Letter):?\s*(.*)$",
    re.IGNORECASE,
)
DRIVE_FILE_ID_RE = re.compile(r"/file/d/([^/?]+)", re.IGNORECASE)
IDEMPOTENCY_PREFIX = "Operation idempotency key: "
PROFILE_FILENAMES = ("applicant_profile.json", "application_profile.json")
MAX_TAB_TITLE_LENGTH = 50


def application_tab_title(company, role):
    """Build a readable Google Docs tab title within the 50-character limit."""
    company = (company or "").strip()
    role = (role or "").strip()
    separator = " / "
    available_role = MAX_TAB_TITLE_LENGTH - len(company) - len(separator)
    if available_role >= len(role):
        return (company + separator + role).strip(" /")
    if available_role > 3:
        return company + separator + role[:available_role - 3].rstrip() + "..."
    return (company[:MAX_TAB_TITLE_LENGTH - 3].rstrip() + "...")[:MAX_TAB_TITLE_LENGTH]


def _unique_tab_title(document, title):
    """Ensure a tab title is unique across the entire Google Docs document."""
    existing_titles = {tab_title(tab) for tab in flatten_tabs(document.get("tabs", []))}
    if title not in existing_titles:
        return title
    # Append a short suffix to make it unique
    suffix = 2
    base = title
    while True:
        candidate = title + " ({})".format(suffix)
        if len(candidate) > MAX_TAB_TITLE_LENGTH:
            candidate = base[:MAX_TAB_TITLE_LENGTH - len(" ({})".format(suffix)) - 3].rstrip() + "...({})".format(suffix)
        if candidate not in existing_titles:
            return candidate
        suffix += 1


class GoogleDocsClient:
    def __init__(self, access_token, document_id):
        self.access_token = access_token
        self.document_id = document_id
        self.timeout = float(os.environ.get('GOOGLE_API_TIMEOUT_SECONDS', '30'))
        self.cache_seconds = float(os.environ.get('JOURNAL_READ_CACHE_SECONDS', '5'))
        if self.timeout <= 0 or self.cache_seconds < 0:
            raise ValueError('Google API timeout must be positive and journal cache duration non-negative')
        self._document = None
        self._document_expires = 0

    def invalidate_document(self):
        self._document = None
        self._document_expires = 0

    def request(self, method, url, payload=None):
        if method != 'GET':
            self.invalidate_document()
        request = urllib.request.Request(url, method=method)
        request.add_header("Authorization", "Bearer " + access_token_value(self.access_token))
        request.add_header("Content-Type", "application/json")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        with urllib.request.urlopen(request, data=data, timeout=self.timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}

    def get_document(self):
        if self._document is not None and monotonic() < self._document_expires:
            return self._document
        query = urllib.parse.urlencode({"includeTabsContent": "true"})
        url = "https://docs.googleapis.com/v1/documents/{}?{}".format(self.document_id, query)
        document = self.request("GET", url)
        self._document = document
        self._document_expires = monotonic() + self.cache_seconds
        return document

    def batch_update(self, requests, revision_id=None):
        # Invalidate even if a response is lost after Google accepts a write.
        self.invalidate_document()
        url = "https://docs.googleapis.com/v1/documents/{}:batchUpdate".format(self.document_id)
        body = {"requests": requests}
        if revision_id:
            body["writeControl"] = {"requiredRevisionId": revision_id}
        try:
            return self.request("POST", url, body)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            # Text positions were computed from this revision. Replaying them
            # against a newer revision could modify somebody else's notes.
            # The caller can rebuild a fresh, idempotent operation on retry.
            raise RuntimeError("Google Docs API HTTP {}: {}".format(error.code, detail))


def fresh_journal_write(method):
    """External edits must be observed before every outermost journal mutation."""
    @wraps(method)
    def write(self, *args, **kwargs):
        depth = getattr(self, '_journal_write_depth', 0)
        if depth == 0 and hasattr(self.client, 'invalidate_document'):
            self.client.invalidate_document()
        self._journal_write_depth = depth + 1
        try:
            return method(self, *args, **kwargs)
        finally:
            self._journal_write_depth = depth
    return write


def tab_properties(tab):
    properties = tab.get("tabProperties")
    if properties:
        return properties
    # The connected Drive/Docs representation may flatten tabProperties into
    # the tab object. The write path still uses the native Docs API shape.
    return {
        key: tab[key]
        for key in ("tabId", "title", "parentTabId", "index")
        if key in tab
    }


def tab_id(tab):
    return tab_properties(tab).get("tabId")


def tab_title(tab):
    return tab_properties(tab).get("title", "")


def child_tabs(tab):
    return tab.get("childTabs") or []


def flatten_tabs(tabs):
    result = []
    for tab in tabs or []:
        result.append(tab)
        result.extend(flatten_tabs(child_tabs(tab)))
    return result


def document_tabs(document):
    return flatten_tabs(document.get("tabs") or [])


def children_of(document, parent_tab):
    """Support both nested Docs API tabs and connector-flattened tab results."""
    parent_id = tab_id(parent_tab)
    children = {}
    for child in child_tabs(parent_tab):
        if tab_id(child):
            children[tab_id(child)] = child
    for candidate in document_tabs(document):
        if tab_properties(candidate).get("parentTabId") == parent_id:
            children[tab_id(candidate)] = candidate
    return list(children.values())


def find_tab(tabs, wanted_id):
    for tab in flatten_tabs(tabs):
        if tab_id(tab) == wanted_id:
            return tab
    raise KeyError("Google Docs tab not found: " + wanted_id)


def state_tabs(document):
    result = {}
    counts = {}
    for tab in document.get("tabs") or []:
        title = tab_title(tab)
        if title in ALL_STATES and not tab_properties(tab).get("parentTabId"):
            counts[title] = counts.get(title, 0) + 1
            result[title] = tab
    missing = [name for name in STATE_TABS if name not in result]
    if missing:
        raise ValueError(
            "journal is missing top-level state tabs: {}. Required tabs: {}".format(
                ", ".join(missing), ", ".join(STATE_TABS)
            )
        )
    duplicates = [name for name in ALL_STATES if counts.get(name, 0) > 1]
    if duplicates:
        raise ValueError("journal has duplicate top-level state tabs: " + ", ".join(duplicates))
    return result


def journal_status(document):
    present = [
        name for name in STATE_TABS
        if any(tab_title(tab) == name and not tab_properties(tab).get("parentTabId")
               for tab in document.get("tabs") or [])
    ]
    missing = [name for name in STATE_TABS if name not in present]
    counts = {}
    for tab in document.get("tabs") or []:
        title = tab_title(tab)
        if title in STATE_TABS and not tab_properties(tab).get("parentTabId"):
            counts[title] = counts.get(title, 0) + 1
    duplicates = [name for name in STATE_TABS if counts.get(name, 0) > 1]
    return {
        "valid": not missing and not duplicates,
        "required_states": list(STATE_TABS),
        "present_states": present,
        "missing_states": missing,
        "duplicate_states": duplicates,
    }


def body_content(tab):
    document_tab = tab.get("documentTab") or {}
    body = document_tab.get("body") or {}
    return body.get("content") or (tab.get("body") or {}).get("content") or []


def utf16_length(value):
    return len(value.encode("utf-16-le")) // 2


def hyperlink_requests(text, start_index, application_id):
    requests = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,)")
        if not url:
            continue
        url_end = match.start() + len(url)
        requests.append({"updateTextStyle": {
            "range": {
                "startIndex": start_index + utf16_length(text[:match.start()]),
                "endIndex": start_index + utf16_length(text[:url_end]),
                "tabId": application_id,
            },
            "textStyle": {"link": {"url": url}},
            "fields": "link",
        }})
    return requests


def tab_end_index(tab):
    content = body_content(tab)
    return max([item.get("endIndex", 1) for item in content] or [1])


def tab_paragraphs(tab):
    paragraphs = []
    for structural in body_content(tab):
        paragraph = structural.get("paragraph")
        if not paragraph:
            continue
        chunks = []
        links = []
        for element in paragraph.get("elements") or []:
            text_run = element.get("textRun") or {}
            chunks.append(text_run.get("content", ""))
            style = text_run.get("textStyle") or {}
            link_url = (style.get("link") or {}).get("url")
            if link_url:
                links.append(link_url)
        is_list_item = bool(paragraph.get("bullet"))
        text = "".join(chunks).rstrip("\n")
        if is_list_item:
            # Some Drive connector responses include the rendered '-' glyph
            # in textRun content, while the native Docs API does not.
            text = re.sub(r"^\s*[-•●○■▪◦]\s+", "", text)
        para = {
            "text": text,
            "isListItem": is_list_item,
            "startIndex": structural.get("startIndex", 0),
            "endIndex": structural.get("endIndex", 0),
        }
        if links:
            para["links"] = links
        paragraphs.append(para)
    return paragraphs


def first_posting_url(events, paragraphs=None):
    for event in events:
        for bullet in event.get("bullets", []):
            match = URL_RE.search(bullet)
            if match:
                url = match.group(0).rstrip(".,)")
                # Skip Drive artifact links; the posting URL is the job-board URL
                if "drive.google.com/file/d/" in url:
                    continue
                return url
    for paragraph in paragraphs or []:
        # Prefer hyperlink URL if the paragraph text is wrapped in a link
        links = paragraph.get("links", [])
        for link in links:
            if "drive.google.com/file/d/" not in link:
                return link
        match = URL_RE.search(paragraph.get("text", ""))
        if match:
            url = match.group(0).rstrip(".,)")
            if "drive.google.com/file/d/" not in url:
                return url
    return ""


def _without_idempotency_marker(bullets):
    return [bullet for bullet in bullets if not bullet.startswith(IDEMPOTENCY_PREFIX)]


def artifact_reference(bullet):
    match = ARTIFACT_RE.match(bullet.strip())
    if match:
        return match.groups()
    legacy = LEGACY_ARTIFACT_RE.match(bullet.strip())
    if not legacy:
        return None
    label, reference = legacy.groups()
    drive_match = DRIVE_FILE_ID_RE.search(reference)
    return label.strip(), drive_match.group(1) if drive_match else None


class GoogleDocsRepository(Repository):
    """Repository that preserves the user's state-tab and journal conventions."""

    def __init__(self, access_token, document_id, artifact_root_url=None,
                 application_root_path=None):
        self.client = GoogleDocsClient(access_token, document_id)
        self.artifact_root_url = artifact_root_url
        self.application_root_path = application_root_path or ""
        self.artifacts = (
            GoogleDriveArtifactStore(access_token, artifact_root_url, self.application_root_path)
            if artifact_root_url else None
        )
        self._idempotency = {}

    def _application_tabs(self, document):
        applications = []
        states = state_tabs(document)
        for state, state_tab in states.items():
            for tab in children_of(document, state_tab):
                applications.append((state, state_tab, tab))
        return applications

    def journal_status(self):
        return journal_status(self.client.get_document())

    def journal_tabs(self):
        return [{'tab_id': tab_id(tab), 'title': tab_title(tab),
                 'parent_tab_id': tab_properties(tab).get('parentTabId'),
                 'paragraphs': tab_paragraphs(tab)}
                for tab in document_tabs(self.client.get_document())]


    @fresh_journal_write
    def repair_journal(self):
        document = self.client.get_document()
        status = journal_status(document)
        missing = status["missing_states"]
        if missing:
            self.client.batch_update([
                {"addDocumentTab": {"tabProperties": {"title": name}}}
                for name in missing
            ], document.get("revisionId"))
            document = self.client.get_document()
        return journal_status(document)

    @fresh_journal_write
    def migrate_application_states(self):
        """Explicit, revision-checked rename preserving all child tabs/history."""
        document = self.client.get_document()
        roots = [t for t in document.get('tabs', []) if not tab_properties(t).get('parentTabId')]
        old = [t for t in roots if tab_title(t) == 'Ready To Apply']
        new = [t for t in roots if tab_title(t) == 'Prepared']
        manual = [t for t in roots if tab_title(t) == 'Manual Apply Required']
        dead = [t for t in roots if tab_title(t) == 'Dead Letters Queue']
        if len(old) > 1 or len(new) > 1 or len(manual) > 1 or len(dead) > 1 or (old and new):
            raise ValueError('ambiguous lifecycle tabs; refusing to merge or delete data')
        if not old and not new:
            raise ValueError('ready state tab is missing; migration requires existing ready tab')
        requests = []
        if old:
            requests.append({'updateDocumentTabProperties': {
                'tabProperties': {'tabId': tab_id(old[0]), 'title': 'Prepared'},
                'fields': 'title',
            }})
        if not manual:
            requests.append({'addDocumentTab': {'tabProperties': {'title': 'Manual Apply Required'}}})
        if not dead:
            requests.append({'addDocumentTab': {'tabProperties': {'title': 'Dead Letters Queue'}}})
        if requests:
            self.client.batch_update(requests, document.get('revisionId'))
        return self.journal_status()

    def _application(self, state, state_tab, tab):
        application_tab_id = tab_id(tab)
        company, role = parse_application_title(tab_title(tab))
        paragraphs = tab_paragraphs(tab)
        events = parse_events(paragraphs, application_tab_id)
        # Tab titles are limited to 50 characters. Creation metadata preserves
        # the complete identity for file lookup, package validation and forms.
        for bullet in (events[0].get('bullets', []) if events else []):
            if bullet.startswith('Company: '):
                company = bullet[len('Company: '):].strip() or company
            elif bullet.startswith('Role: '):
                role = bullet[len('Role: '):].strip() or role
        # Later correction events can override the company/role
        for event in events:
            for bullet in event.get('bullets', []):
                if bullet.startswith('Corrected company to: '):
                    company = bullet[len('Corrected company to: '):].strip()
                elif bullet.startswith('Corrected role to: '):
                    role = bullet[len('Corrected role to: '):].strip()
        last_event = events[-1] if events else None
        return Application(
            application_id=application_tab_id,
            company=company,
            role=role,
            posting_url=first_posting_url(events, paragraphs),
            status=state,
            created_at=(journal_timestamp(events[0]["date"]) if events else now()),
            updated_at=(journal_timestamp(last_event["date"]) if last_event else now()),
            last_event_id=(last_event["event_id"] if last_event else None),
        )

    def _find_application(self, document, application_id):
        for state, state_tab, tab in self._application_tabs(document):
            if tab_id(tab) == application_id:
                return state, state_tab, tab
        raise KeyError(application_id)

    def _ensure_optional_state_tab(self, document, state_name):
        """Create a missing optional state tab and return it.
        Must be called within a @fresh_journal_write context so the tab
        is created in the same batch as the subsequent operation.
        """
        title = _unique_tab_title(document, state_name)
        self.client.batch_update([
            {"addDocumentTab": {"tabProperties": {"title": title}}}
        ], document.get("revisionId"))
        # Refresh the document to get the newly created tab
        document = self.client.get_document()
        for tab in document.get("tabs", []):
            if tab_title(tab) == state_name and not tab_properties(tab).get("parentTabId"):
                return tab
        raise KeyError(f"failed to create optional state tab: {state_name}")

    def _state_tabs(self, document):
        """Return state tabs, auto-creating any missing optional ones.
        Must be called within a @fresh_journal_write context so tabs
        can be created in the same batch as the subsequent operation.
        """
        tabs_map = state_tabs(document)
        for state_name in OPTIONAL_STATE_TABS:
            if state_name not in tabs_map:
                tabs_map[state_name] = self._ensure_optional_state_tab(document, state_name)
        return tabs_map

    def _find_by_posting_url(self, document, posting_url):
        posting_url = normalize_posting_url(posting_url)
        for state, state_tab, tab in self._application_tabs(document):
            application = self._application(state, state_tab, tab)
            if normalize_posting_url(application.posting_url) == posting_url:
                return state, state_tab, tab
        return None

    def _idempotent_event(self, tab, application_id, idempotency_key):
        if not idempotency_key:
            return None
        marker = IDEMPOTENCY_PREFIX + idempotency_key
        for event in parse_events(tab_paragraphs(tab), application_id):
            if marker in event.get("bullets", []):
                return Event(event["event_id"], application_id, "journal_event", journal_timestamp(event["date"]), {
                    "display_date": event["display_date"],
                    "bullets": _without_idempotency_marker(event["bullets"]),
                })
        return None

    @fresh_journal_write
    def create_application(self, company, role, posting_url, target_state="Bookmarked", data=None, idempotency_key=None):
        cached = self._idempotency.get(idempotency_key) if idempotency_key else None
        if cached:
            return cached
        posting_url = normalize_posting_url(posting_url)
        document = self.client.get_document()
        existing = self._find_by_posting_url(document, posting_url)
        if existing:
            result = self._application(*existing)
            if idempotency_key:
                self._idempotency[idempotency_key] = result
            return result
        if target_state == 'Discovered' and len(document_tabs(document)) >= 100:
            return None
        state_tabs_map = self._state_tabs(document)
        state_tab = state_tabs_map.get(target_state) or state_tabs_map["Bookmarked"]
        title = application_tab_title(company, role)
        created = None
        state_tabs_children = children_of(document, state_tab)
        # A keyed retry may be recovering a tab created before the process
        # failed while appending its first event. Only reuse an uninitialized
        # matching tab, and only when the caller supplied an idempotency key;
        # unkeyed creates retain their normal new-tab behavior.
        if idempotency_key:
            unfinished = [
                tab for tab in state_tabs_children
                if tab_title(tab) == title and not parse_events(tab_paragraphs(tab), tab_id(tab))
            ]
            if len(unfinished) == 1:
                created = unfinished[0]
        if not created:
            # Ensure tab title is unique across the entire document to avoid
            # Google Docs "Tab title must be unique" errors.
            title = _unique_tab_title(document, title)
            existing_tab_ids = {tab_id(tab) for tab in state_tabs_children}
            self.client.batch_update([{"addDocumentTab": {
                "tabProperties": {"title": title, "parentTabId": tab_id(state_tab)}
            }}], document.get("revisionId"))
            document = self.client.get_document()
            refreshed_children = children_of(document, state_tab)
            new_tabs = [tab for tab in refreshed_children if tab_id(tab) not in existing_tab_ids]
            if len(new_tabs) == 1:
                created = new_tabs[0]
            else:
                for tab in refreshed_children:
                    if tab_title(tab) == title:
                        created = tab
                        break
        if not created:
            raise RuntimeError("created application tab could not be located")
        bullets = ["Posting URL: " + posting_url, 'Company: ' + company, 'Role: ' + role]
        if (data or {}).get("bullets"):
            bullets.extend(data["bullets"])
        if (data or {}).get("relevance_evidence"):
            bullets.extend("Relevance: " + str(evidence) for evidence in data["relevance_evidence"])
        self.append_event(tab_id(created), "application_created", {
            "date": str(date.today()),
            "bullets": bullets,
            "source": (data or {}).get("source", "data layer"),
        }, idempotency_key)
        result = self.get_application(tab_id(created))
        if idempotency_key:
            self._idempotency[idempotency_key] = result
        return result

    def get_application(self, application_id):
        document = self.client.get_document()
        state, state_tab, tab = self._find_application(document, application_id)
        return self._application(state, state_tab, tab)

    @fresh_journal_write
    def update_application(self, application_id, company=None, role=None):
        """Update company and/or role for an existing application, including the tab title and event."""
        document = self.client.get_document()
        state, state_tab, tab = self._find_application(document, application_id)
        current_app = self._application(state, state_tab, tab)
        new_company = company if company is not None else current_app.company
        new_role = role if role is not None else current_app.role
        requests = []
        # Update the tab title
        new_title = application_tab_title(new_company, new_role)
        new_title = _unique_tab_title(document, new_title)
        if tab_title(tab) != new_title:
            requests.append({
                "updateDocumentTabProperties": {
                    "tabProperties": {"tabId": application_id, "title": new_title},
                    "fields": "title",
                }
            })
        # Append a correction event in the same batch as the tab title update
        events = parse_events(tab_paragraphs(tab), application_id)
        if events:
            first_event = events[0]
            bullets = first_event.get("bullets", [])
            updated_bullets = []
            for bullet in bullets:
                if bullet.startswith("Company: "):
                    updated_bullets.append("Company: " + new_company)
                elif bullet.startswith("Role: "):
                    updated_bullets.append("Role: " + new_role)
                else:
                    updated_bullets.append(bullet)
            if updated_bullets != bullets:
                # Build the correction event using the canonical event format
                # so it shares the same batch_update revision as the tab title change.
                correction_bullets = [
                    "Corrected company to: " + new_company,
                    "Corrected role to: " + new_role,
                ]
                event_date = str(date.today())
                separator = "\n\n"
                event_text_str = build_event_text(event_date, correction_bullets, separator)
                start = max(1, tab_end_index(tab) - 1)
                inserted_end = start + utf16_length(event_text_str)
                bullet_start = start + utf16_length(separator + event_date + "\n\n")
                bullet_end = bullet_start + utf16_length("\n".join(correction_bullets) + "\n")
                requests.extend([
                    {"insertText": {"location": {"index": start, "tabId": application_id}, "text": event_text_str}},
                    {"deleteParagraphBullets": {"range": {"startIndex": start, "endIndex": inserted_end + 1, "tabId": application_id}}},
                    {"createParagraphBullets": {
                        "range": {"startIndex": bullet_start, "endIndex": bullet_end, "tabId": application_id},
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }},
                ])
        if requests:
            self.client.batch_update(requests, document.get("revisionId"))
        result = self.get_application(application_id)
        return result

    def list_main_journal_applications(self, statuses):
        """Applications on the main journal, filtered by status."""
        document = self.client.get_document()
        wanted = set(statuses)
        return [self._application(*entry) for entry in self._application_tabs(document) if entry[0] in wanted]

    def list_applications(self, status=None):
        document = self.client.get_document()
        applications = [self._application(*entry) for entry in self._application_tabs(document)]
        return [app for app in applications if status is None or app.status == status]

    def list_events(self, application_id):
        document = self.client.get_document()
        state, state_tab, tab = self._find_application(document, application_id)
        events = parse_events(tab_paragraphs(tab), application_id)
        return [Event(event["event_id"], application_id, "journal_event", journal_timestamp(event["date"]), {
            "display_date": event["display_date"],
            "bullets": _without_idempotency_marker(event["bullets"]),
        }) for event in events]

    @fresh_journal_write
    def append_event(self, application_id, event_type, data=None, idempotency_key=None):
        if idempotency_key and idempotency_key in self._idempotency:
            return self._idempotency[idempotency_key]
        document = self.client.get_document()
        state, state_tab, tab = self._find_application(document, application_id)
        durable = self._idempotent_event(tab, application_id, idempotency_key)
        if durable:
            self._idempotency[idempotency_key] = durable
            return durable
        return self._append_event_batch(document, tab, application_id, event_type, data, idempotency_key)

    def _append_event_batch(self, document, tab, application_id, event_type, data=None,
                            idempotency_key=None, additional_requests=(), cache_result=True):
        payload = data or {}
        event_date = str(payload.get("date", date.today()))
        if not is_date_paragraph(event_date):
            raise ValueError("journal event date must be YYYY-MM-DD or a supported display date")
        bullets = payload.get("bullets") or [event_type]
        bullets = [re.sub(r"^[-•]\s*", "", str(bullet)).strip() for bullet in bullets if str(bullet).strip()]
        if not bullets:
            raise ValueError("journal events require at least one non-empty bullet")
        # The final body newline belongs to the existing last paragraph. Add
        # an unlisted separator before the new date so it cannot be merged
        # into the preceding list item.
        start = max(1, tab_end_index(tab) - 1)
        persisted_bullets = list(bullets)
        # Package preparation is idempotent at the stage boundary: the
        # runner checks for an existing package-prepared event and artifact.
        # Do not expose an implementation idempotency key in that user-facing
        # event; it made the journal noisy.
        if idempotency_key and event_type != "application_package_prepared":
            persisted_bullets.append(IDEMPOTENCY_PREFIX + idempotency_key)
        separator = "\n\n"
        text = build_event_text(event_date, persisted_bullets, separator)
        date_start = start + utf16_length(separator)
        date_end = date_start + utf16_length(event_date + "\n\n")
        bullet_start = date_end
        # Include the final bullet's paragraph terminator, but exclude the
        # blank separator paragraph. This keeps the last bullet listed without
        # creating an empty bullet below it.
        bullet_end = bullet_start + utf16_length("\n".join(persisted_bullets) + "\n")
        inserted_end = start + utf16_length(text)
        requests = list(additional_requests) + [
            {"insertText": {"location": {"index": start, "tabId": application_id}, "text": text}},
            # Inserted paragraphs inherit the list style at the insertion
            # point. Clear it across the whole insertion, then add bullets
            # only to the event's bullet paragraphs.
            {"deleteParagraphBullets": {"range": {"startIndex": start, "endIndex": inserted_end + 1, "tabId": application_id}}},
            {"createParagraphBullets": {
                "range": {"startIndex": bullet_start, "endIndex": bullet_end, "tabId": application_id},
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
            }},
        ]
        requests.extend(hyperlink_requests(text, start, application_id))
        self.client.batch_update(requests, document.get("revisionId"))
        event = Event("evt_" + uuid.uuid4().hex, application_id, event_type, journal_timestamp(event_date), {
            "display_date": event_date,
            "bullets": bullets,
            "source": payload.get("source", "data layer"),
        })
        if idempotency_key and cache_result:
            self._idempotency[idempotency_key] = event
        return event

    @fresh_journal_write
    def repair_application_journal(self, application_id):
        """Rebuild one application tab with safe paragraph and bullet ranges."""
        document = self.client.get_document()
        _state, _state_tab, tab = self._find_application(document, application_id)
        parsed = parse_events(tab_paragraphs(tab), application_id)
        events = []
        for event in parsed:
            bullets = [
                str(item).strip() for item in event.get("bullets", [])
                if str(item).strip() and not str(item).strip().startswith(IDEMPOTENCY_PREFIX)
            ]
            if bullets:
                events.append((event["display_date"], bullets))
        if not events:
            return {"application_id": application_id, "events_repaired": 0}

        text_parts = []
        bullet_ranges = []
        offset = 0
        for event_date, bullets in events:
            body = event_date + "\n\n" + "\n".join(bullets) + "\n"
            text_parts.append(body)
            bullet_start = 1 + utf16_length("".join(text_parts[:-1])) + utf16_length(event_date + "\n\n")
            bullet_end = bullet_start + utf16_length("\n".join(bullets) + "\n")
            bullet_ranges.append((bullet_start, bullet_end))
            offset += utf16_length(body)
            text_parts.append("\n")
            offset += 1
        # Keep separators only between events; the final event needs its
        # paragraph terminator but not an additional blank paragraph.
        clean_text = "".join(text_parts[:-1]) + "\n"
        old_end = max(1, tab_end_index(tab) - 1)
        requests = []
        if old_end > 1:
            requests.append({"deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": old_end, "tabId": application_id}
            }})
        requests.append({"insertText": {
            "location": {"index": 1, "tabId": application_id}, "text": clean_text
        }})
        requests.append({"deleteParagraphBullets": {
            "range": {"startIndex": 1, "endIndex": 2 + utf16_length(clean_text), "tabId": application_id}
        }})
        requests.extend({"createParagraphBullets": {
            "range": {"startIndex": start, "endIndex": end, "tabId": application_id},
            "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
        }} for start, end in bullet_ranges)
        requests.extend(hyperlink_requests(clean_text, 1, application_id))
        self.client.batch_update(requests, document.get("revisionId"))
        return {"application_id": application_id, "events_repaired": len(events)}

    @fresh_journal_write
    def delete_last_event(self, application_id):
        """Delete the last event block from an application tab.

        Finds the last event by parsing paragraphs, then deletes the content
        range from the event's start to the end of the tab.
        """
        document = self.client.get_document()
        _state, _state_tab, tab = self._find_application(document, application_id)
        paragraphs = tab_paragraphs(tab)
        events = parse_events(paragraphs, application_id)
        if not events:
            return {"application_id": application_id, "deleted": False, "reason": "no events"}
        # Find the last event that is not the first (scout) event
        if len(events) < 2:
            return {"application_id": application_id, "deleted": False, "reason": "only one event"}
        last_event = events[-1]
        last_start = last_event.get("start_index", 0)
        end = max(2, tab_end_index(tab) - 1)
        if last_start < 1:
            return {"application_id": application_id, "deleted": False, "reason": "invalid start index"}
        # Also delete any trailing blank paragraph after the last event
        requests = [
            {"deleteContentRange": {
                "range": {"startIndex": last_start, "endIndex": end, "tabId": application_id}
            }},
        ]
        self.client.batch_update(requests, document.get("revisionId"))
        return {"application_id": application_id, "deleted": True, "event_date": last_event.get("display_date")}

    @fresh_journal_write
    def rollback_ready_application(self, application_id):
        """Move a ready posting back to Bookmarked and retain only its scout event.

        This migration changes only the application tab and parent state tab.
        Stored Drive artifacts are intentionally untouched.
        """
        document = self.client.get_document()
        current_state, _state_tab, tab = self._find_application(document, application_id)
        if current_state != "Prepared":
            raise ValueError("rollback requires Prepared: {}".format(current_state))
        events = parse_events(tab_paragraphs(tab), application_id)
        posting_url = first_posting_url(events, tab_paragraphs(tab))
        bullets = []
        if posting_url:
            bullets.append("Posting URL: " + posting_url)
        bullets.append("Added by Job Scout")
        event_date = events[0]["display_date"] if events else str(date.today())
        clean_text = event_date + "\n\n" + "\n".join(bullets) + "\n"
        old_end = max(1, tab_end_index(tab) - 1)
        requests = []
        if old_end > 1:
            requests.append({"deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": old_end, "tabId": application_id}
            }})
        requests.extend([
            {"insertText": {
                "location": {"index": 1, "tabId": application_id}, "text": clean_text
            }},
            {"deleteParagraphBullets": {
                "range": {"startIndex": 1, "endIndex": 2 + utf16_length(clean_text), "tabId": application_id}
            }},
            {"createParagraphBullets": {
                "range": {
                    "startIndex": 1 + utf16_length(event_date + "\n\n"),
                    "endIndex": 1 + utf16_length(clean_text),
                    "tabId": application_id,
                },
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
            }},
            {"updateDocumentTabProperties": {
                "tabProperties": {
                    "tabId": application_id,
                    "parentTabId": tab_id(self._state_tabs(document)["Bookmarked"]),
                },
                "fields": "parentTabId,index",
            }},
        ])
        requests.extend(hyperlink_requests(clean_text, 1, application_id))
        self.client.batch_update(requests, document.get("revisionId"))
        return {
            "application_id": application_id,
            "status": "Bookmarked",
            "events_remaining": 1,
            "artifacts_preserved": True,
        }

    @fresh_journal_write
    def delete_application(self, application_id):
        """Delete one application tab and its nested tabs from the journal."""
        document = self.client.get_document()
        state, _parent, tab = self._find_application(document, application_id)
        self.client.batch_update([
            {"deleteTab": {"tabId": application_id}},
        ], document.get("revisionId"))
        return {
            "application_id": application_id,
            "company": parse_application_title(tab_title(tab))[0],
            "status_before_delete": state,
            "deleted": True,
        }

    def export_for_completion(self, application_id):
        try:
            app = self.get_application(application_id)
        except KeyError:
            return None
        lines = []
        for event in self.list_events(application_id):
            data = event.data if hasattr(event, "data") else {}
            lines.append(str((data or {}).get("date") or ""))
            for bullet in (data or {}).get("bullets") or []:
                lines.append(str(bullet))
        return {
            "tab_name": "{} / {}".format(app.company, app.role).strip(" /"),
            "company": app.company,
            "role": app.role,
            "journal_text": "\n".join(lines),
            "research": self.list_research(application_id),
        }

    def remove_tab(self, application_id):
        try:
            self.client.batch_update([{"deleteTab": {"tabId": application_id}}])
        except Exception as exc:
            if "not found" in str(exc).lower() or "404" in str(exc):
                return
            raise

    def complete_application(self, application_id, reason):
        return {"application_id": application_id, "reason": reason, "status": reason}

    @fresh_journal_write
    def transition(self, application_id, target, data=None, idempotency_key=None):
        cached = self._idempotency.get(idempotency_key) if idempotency_key else None
        if cached:
            return cached
        document = self.client.get_document()
        current_state, current_parent, tab = self._find_application(document, application_id)
        durable = self._idempotent_event(tab, application_id, idempotency_key)
        if durable:
            result = self.get_application(application_id)
            if idempotency_key:
                self._idempotency[idempotency_key] = result
            return result
        if current_state == target:
            # The tab move may have committed before the event append was
            # interrupted. On retry, preserve the journal invariant instead
            # of treating the already-moved tab as a complete operation.
            if idempotency_key:
                detail = [str(item) for item in (data or {}).get("bullets") or [] if str(item).strip()]
                # The tab is already in the target state, so the earlier source
                # is gone. The event still starts with "{source} --> {target}".
                bullets = with_transition_first(detail, current_state, target)
                self._append_event_batch(document, tab, application_id, "status_changed", {
                    "date": str(date.today()),
                    "bullets": bullets,
                    "source": (data or {}).get("source", "data layer"),
                }, idempotency_key, cache_result=False)
                result = self.get_application(application_id)
                self._idempotency[idempotency_key] = result
                return result
            result = self._application(current_state, current_parent, tab)
            return result
        validate_transition(current_state, target)

        tabs_map = self._state_tabs(document)
        target_parent = tabs_map.get(target)
        if target_parent is None:
            raise KeyError(target)
        move = {"updateDocumentTabProperties": {
            "tabProperties": {
                "tabId": application_id,
                "parentTabId": tab_id(target_parent),
            },
            "fields": "parentTabId,index",
        }}
        # The state move and its explanation commit in one revision-checked
        # batch, avoiding a second full-journal read and a second write.
        detail = [str(item) for item in (data or {}).get("bullets") or [] if str(item).strip()]
        self._append_event_batch(document, tab, application_id, "status_changed", {
            "date": str(date.today()),
            "bullets": with_transition_first(detail, current_state, target),
            "source": (data or {}).get("source", "data layer"),
        }, idempotency_key, additional_requests=[move], cache_result=False)
        result = self.get_application(application_id)
        if idempotency_key:
            self._idempotency[idempotency_key] = result
        from services.activity_log import log_transition
        if isinstance(result, dict):
            company, role = result.get("company"), result.get("role")
        else:
            company, role = getattr(result, "company", ""), getattr(result, "role", "")
        log_transition(company, role, current_state, target)
        return result

    def save_artifact(self, application_id, name, drive_file_id=None, mime_type=None, idempotency_key=None):
        # A bookmark records the posting link on its one transition event.
        # A second artifact event would add another date heading.
        if not str(name or "").startswith("Job Posting - "):
            reference = "Artifact: " + name
            if drive_file_id:
                reference += " (Drive file: " + drive_file_id + ")"
            self.append_event(application_id, "artifact_saved", {
                "date": str(date.today()),
                "bullets": [reference],
                "source": "data layer",
            }, idempotency_key)
        return Artifact("artifact_" + uuid.uuid4().hex, application_id, name, drive_file_id, mime_type)

    @fresh_journal_write
    def store_artifact(self, application_id, name, content, mime_type=None, idempotency_key=None):
        if not self.artifacts:
            raise RuntimeError("artifact root is not configured")
        cached = self._idempotency.get(idempotency_key) if idempotency_key else None
        if cached:
            return cached
        application = self.get_application(application_id)
        stored = self.artifacts.save(
            application.company,
            application.role,
            name,
            content,
            mime_type,
            idempotency_key,
        )
        # The Drive upload is durable even if the caller loses the response
        # before the journal reference is written. Reconcile that case before
        # appending another human-visible event on retry.
        for existing in self.list_artifacts(application_id):
            if existing.drive_file_id and existing.drive_file_id == stored.get("id"):
                if idempotency_key:
                    self._idempotency[idempotency_key] = existing
                return existing
        artifact = self.save_artifact(
            application_id,
            name,
            stored.get("id"),
            stored.get("mimeType") or mime_type,
            idempotency_key,
        )
        if idempotency_key:
            self._idempotency[idempotency_key] = artifact
        return artifact

    @fresh_journal_write
    def write_research(self, application_id, title, content, idempotency_key=None):
        cached = self._idempotency.get(idempotency_key) if idempotency_key else None
        if cached:
            return cached
        document = self.client.get_document()
        state, parent, application_tab = self._find_application(document, application_id)
        for child in children_of(document, application_tab):
            if tab_title(child) == title:
                result = {"application_id": application_id, "title": title, "research_tab_id": tab_id(child)}
                if idempotency_key:
                    self._idempotency[idempotency_key] = result
                return result
        self.client.batch_update([{"addDocumentTab": {
            "tabProperties": {"title": title, "parentTabId": application_id}
        }}], document.get("revisionId"))
        document = self.client.get_document()
        research_tab = None
        for child in children_of(document, find_tab(document_tabs(document), application_id)):
            if tab_title(child) == title:
                research_tab = child
                break
        if not research_tab:
            raise RuntimeError("created research tab could not be located")
        self.client.batch_update([{"insertText": {
            "location": {"index": 1, "tabId": tab_id(research_tab)},
            "text": str(content).rstrip() + "\n",
        }}], document.get("revisionId"))
        result = {"application_id": application_id, "title": title, "research_tab_id": tab_id(research_tab)}
        if idempotency_key:
            self._idempotency[idempotency_key] = result
        return result

    def list_research(self, application_id):
        document = self.client.get_document()
        state, parent, application_tab = self._find_application(document, application_id)
        result = []
        # The native Docs API returns nested ``childTabs`` in some responses,
        # while the connected Drive representation may flatten all tabs and
        # expose only ``parentTabId``.  Use the same resolver as application
        # discovery so research reads are representation-independent.
        for child in children_of(document, application_tab):
            paragraphs = tab_paragraphs(child)
            content = "\n".join(paragraph["text"] for paragraph in paragraphs).strip()
            result.append({
                "application_id": application_id,
                "title": tab_title(child),
                "research_tab_id": tab_id(child),
                "content": content,
            })
        return result

    def list_artifacts(self, application_id):
        document = self.client.get_document()
        state, parent, tab = self._find_application(document, application_id)
        application = self._application(state, parent, tab)
        artifacts = []
        seen_file_ids = set()
        for event in parse_events(tab_paragraphs(tab), application_id):
            for bullet in event.get("bullets", []):
                reference = artifact_reference(bullet)
                if not reference:
                    continue
                name, drive_file_id = reference
                stable_id = "drive:" + drive_file_id if drive_file_id else "journal:" + uuid.uuid5(uuid.NAMESPACE_URL, name).hex
                artifacts.append(Artifact(stable_id, application_id, name, drive_file_id))
                if drive_file_id:
                    seen_file_ids.add(drive_file_id)
        # Journal references are useful for historical files, but the Drive
        # folder is authoritative after journal cleanup/migration.
        if self.artifacts and hasattr(self.artifacts, "list_application_files"):
            for file in self.artifacts.list_application_files(application.company, application.role):
                file_id = file.get("id")
                if not file_id or file_id in seen_file_ids or file.get("mimeType") == "application/vnd.google-apps.folder":
                    continue
                artifacts.append(Artifact(
                    "drive:" + file_id,
                    application_id,
                    file.get("name") or "Drive artifact",
                    file_id,
                    file.get("mimeType"),
                ))
        return artifacts

    def read_artifact(self, application_id, artifact_id):
        for artifact in self.list_artifacts(application_id):
            if artifact.artifact_id == artifact_id or artifact.drive_file_id == artifact_id:
                if not artifact.drive_file_id or not self.artifacts:
                    raise KeyError(artifact_id)
                return {"artifact": artifact, "content": self.artifacts.read(artifact.drive_file_id)}
        raise KeyError(artifact_id)

    def store_file(self, path, content, mime_type=None, idempotency_key=None):
        if not self.artifacts:
            raise RuntimeError("artifact root is not configured")
        normalized = normalize_file_path(path)
        stored = self.artifacts.save_path(normalized, content, mime_type, idempotency_key)
        return {
            "file_id": stored.get("id"),
            "path": normalized,
            "name": stored.get("name") or normalized.rsplit("/", 1)[-1],
            "mime_type": stored.get("mimeType") or mime_type,
        }

    def read_file(self, path):
        if not self.artifacts:
            raise RuntimeError("artifact root is not configured")
        normalized = normalize_file_path(path)
        result = self.artifacts.read_path(normalized)
        metadata = result["file"]
        return {
            "file": {
                "file_id": metadata.get("id"),
                "path": normalized,
                "name": metadata.get("name") or normalized.rsplit("/", 1)[-1],
                "mime_type": metadata.get("mimeType"),
            },
            "content": result["content"],
        }

    def get_profile(self):
        if not self.artifacts:
            raise RuntimeError("artifact root is not configured")
        from .storage_layout import PROFILE_PATH, LEGACY_PROFILE_PATH
        for path in (PROFILE_PATH, LEGACY_PROFILE_PATH):
            try:
                result = self.artifacts.read_path(path)
                return validate_profile(json.loads(result['content'].decode('utf-8')))
            except KeyError:
                pass
        for filename in PROFILE_FILENAMES:
            raw = self.artifacts.read_root(filename)
            if raw is not None:
                return validate_profile(json.loads(raw.decode("utf-8")))
        return None

    def save_profile(self, profile):
        validate_profile(profile)
        if not self.artifacts:
            raise RuntimeError("artifact root is not configured")
        raw = json.dumps(profile, indent=2).encode("utf-8")
        from .storage_layout import PROFILE_PATH
        self.artifacts.migrate_profile()
        return self.artifacts.save_path(PROFILE_PATH, raw, 'application/json')

    def get_stage_execution(self):
        if not self.artifacts:
            return None
        from .storage_layout import STAGE_EXECUTION_PATH
        try:
            result = self.artifacts.read_path(STAGE_EXECUTION_PATH)
            return json.loads(result['content'].decode('utf-8'))
        except KeyError:
            return None

    def set_stage_execution(self, config):
        if not self.artifacts:
            raise RuntimeError("artifact root is not configured")
        from .storage_layout import STAGE_EXECUTION_PATH
        raw = json.dumps(config, indent=2).encode("utf-8")
        self.artifacts.save_path(STAGE_EXECUTION_PATH, raw, 'application/json')
        return config

    def migrate_internal_storage(self):
        if not self.artifacts:
            raise RuntimeError('artifact root is not configured')
        return self.artifacts.migrate_internal_storage()


def document_id_from_url(value):
    # Google commonly emits both /document/d/<id> and /document/u/0/d/<id>
    # links.  Keep accepting a raw document ID for environment-based setups.
    match = re.search(r"/document/(?:u/\d+/)?d/([^/?]+)", value or "")
    return match.group(1) if match else value


def repository_from_environment():
    access_token = token_from_environment()
    document = os.environ.get("GOOGLE_INTERVIEW_JOURNAL_URL") or os.environ.get("GOOGLE_DOCUMENT_ID")
    artifact_root = os.environ.get("GOOGLE_ARTIFACT_ROOT_URL")
    application_root_path = os.environ.get("GOOGLE_APPLICATION_ROOT_PATH")
    if not document or not artifact_root:
        runtime_path = os.environ.get(
            "RUNTIME_CONFIG_PATH",
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config", "runtime.json")),
        )
        try:
            with open(runtime_path) as handle:
                runtime = json.load(handle)
            from services.runtime_config import validate
            validate(runtime)
            drive = runtime.get("google_drive", {})
            document = document or drive.get("interview_journal_url")
            artifact_root = artifact_root or drive.get("artifact_root_url")
            application_root_path = application_root_path or drive.get("application_root_path")
        except IOError:
            pass
    if not access_token or not document:
        raise RuntimeError(
            "Missing Google credentials or GOOGLE_INTERVIEW_JOURNAL_URL; set "
            "GOOGLE_ACCESS_TOKEN or refresh-token credentials"
        )
    return GoogleDocsRepository(
        access_token,
        document_id_from_url(document),
        artifact_root,
        application_root_path,
    )
