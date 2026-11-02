"""One posting URL, mocked board pages, every pipeline agent, one form post.

The posting page is the shell a board serves. A page script renders it into the
description and the application form. The form endpoint is mocked too. Nothing
is submitted to a live employer.
"""
import json
from pathlib import Path

import pytest

from adapters.job_boards.form_flow import PostingReader, PreparedFormSubmission, THANK_YOU
from adapters.job_boards.page_scripts import PageRenderer
from adapters.job_boards.scripted_site import ScriptedSite
from ajss.worker.dispatch import WorkloadOrchestrator
from ajss.worker.handlers import build_handlers
from ajss.queue.policy import StateTransitionEngine
from ajss.queue import GDTQ, find_tab, prop
from ajss.worker.runtime import ApplicationContextLoader
from ajss.queue.store import WorkIndex

BODY = (
    "The Senior Backend Engineer designs Python services backed by PostgreSQL and Kafka. "
) * 12
QUEUES = (
    "Discovered", "Bookmarked", "Validated", "Prepared", "Applied",
    "Manual Apply Required", "Dead Letters Queue",
)


def shell(marker, payload):
    script = marker + json.dumps(payload)
    return (
        "<!DOCTYPE html><html><body><div id=\"apply-root\"></div><script>{}</script></body></html>"
    ).format(script)


def comeet(apply_url):
    url = "https://www.comeet.com/jobs/fixtureco/T1.00/senior-backend-engineer/P1.00"
    payload = {
        "name": "Senior Backend Engineer",
        "company_name": "Fixture Co",
        "location": {"city": "Tel Aviv", "country": "Israel"},
        "custom_fields": {"details": [{"name": "The role", "value": "<p>{}</p>".format(BODY)}]},
        "apply": {"action": apply_url, "fields": ["name", "email", "resume"]},
    }
    return url, shell("POSITION_DATA = ", payload), apply_url


def greenhouse(apply_url):
    url = "https://boards.greenhouse.io/fixtureco/jobs/401"
    payload = {
        "title": "Senior Backend Engineer",
        "company": "Fixture Co",
        "content": "<p>{}</p>".format(BODY),
        "apply": {"action": apply_url, "fields": ["name", "email", "resume"]},
    }
    return url, shell("window.__GREENHOUSE_JOB__ = ", payload), apply_url


def lever(apply_url):
    url = "https://jobs.lever.co/fixtureco/11111111-1111-1111-1111-111111111111"
    payload = {
        "text": "Senior Backend Engineer",
        "company": "Fixture Co",
        "descriptionPlain": BODY,
        "apply": {"action": apply_url, "fields": ["name", "email", "resume"]},
    }
    return url, shell("window.__LEVER_POSTING__ = ", payload), apply_url


def ashby(apply_url):
    url = "https://jobs.ashbyhq.com/fixtureco/22222222-2222-2222-2222-222222222222"
    payload = {
        "title": "Senior Backend Engineer",
        "organization": "Fixture Co",
        "descriptionHtml": "<p>{}</p>".format(BODY),
        "apply": {"action": apply_url, "fields": ["name", "email", "resume"]},
    }
    return url, shell("window.__ASHBY_JOB__ = ", payload), apply_url


BOARDS = {
    "comeet": (comeet, "https://www.comeet.co/careers-api/1.0/company/T1.00/positions/P1.00/apply"),
    "greenhouse": (greenhouse, "https://boards.greenhouse.io/fixtureco/jobs/401/apply"),
    "lever": (lever, "https://jobs.lever.co/fixtureco/11111111-1111-1111-1111-111111111111/apply"),
    "ashby": (ashby, "https://jobs.ashbyhq.com/fixtureco/22222222-2222-2222-2222-222222222222/application"),
}


def tab(tab_id, title, parent=None, text=""):
    properties = {"tabId": tab_id, "title": title, "index": 0}
    if parent:
        properties["parentTabId"] = parent
    content = []
    if text:
        content.append({"paragraph": {"elements": [{"textRun": {"content": text + "\n"}}]}})
    return {"tabProperties": properties, "documentTab": {"body": {"content": content}}, "childTabs": []}


class MemoryDocument:
    document_id = "pipeline-doc"

    def __init__(self, posting_url):
        discovered = tab("q-discovered", "Discovered")
        discovered["childTabs"].append(
            tab("item-1", "Fixture Co / Senior Backend Engineer", "q-discovered", posting_url))
        self.document = {
            "revisionId": "0",
            "tabs": [discovered] + [tab("q-" + name, name) for name in QUEUES if name != "Discovered"],
        }

    def invalidate_document(self):
        return None

    def get_document(self):
        import copy
        return copy.deepcopy(self.document)

    def batch_update(self, requests, revision_id=None):
        if revision_id is not None and revision_id != self.document["revisionId"]:
            raise ValueError("Concurrent revision")
        for request in requests:
            if "addDocumentTab" in request:
                properties = request["addDocumentTab"]["tabProperties"]
                created = tab("tab-" + properties["title"], properties["title"], properties.get("parentTabId"))
                parent = find_tab(self.document, properties.get("parentTabId")) if properties.get("parentTabId") else None
                (parent["childTabs"] if parent else self.document["tabs"]).append(created)
            elif "updateDocumentTabProperties" in request:
                change = request["updateDocumentTabProperties"]
                target = find_tab(self.document, change["tabProperties"]["tabId"])
                for key in change["fields"].split(","):
                    target["tabProperties"][key.strip()] = change["tabProperties"][key.strip()]
            elif "insertText" in request:
                change = request["insertText"]
                target = find_tab(self.document, change["endOfSegmentLocation"]["tabId"])
                target["documentTab"]["body"]["content"].append(
                    {"paragraph": {"elements": [{"textRun": {"content": change["text"]}}]}})
            else:
                raise AssertionError(request)
        self.document["revisionId"] = str(int(self.document["revisionId"]) + 1)
        return {"replies": []}


class MemoryStore:
    def __init__(self):
        self.files = {}

    def save_path(self, path, content, mime_type=None, idempotency_key=None):
        self.files[path] = content
        return {"id": path}

    def read_path(self, path):
        return {"content": self.files[path]}


def resume_pdf():
    from ajss.documents.generator import _pdf_bytes
    return _pdf_bytes(["Fixture Applicant", "Python, PostgreSQL, and Kafka."])


def cover_letter(_context):
    return (
        "Dear Fixture Co,\n\n"
        "I am applying for the Senior Backend Engineer role. "
        "I have shipped Python services on PostgreSQL and Kafka.\n\n"
        "Fixture Applicant"
    )


def queue_name(queue, item_id):
    document = queue.snapshot()
    parent = prop(find_tab(document, item_id), "parentTabId")
    return prop(find_tab(document, parent), "title")


def run_pipeline(tmp_path, posting_url, page, apply_url, apply_body):
    site = ScriptedSite()
    site.route("GET", posting_url, page)
    site.route("POST", apply_url, apply_body, content_type="text/plain")
    renderer = PageRenderer()
    rendered = renderer.render(posting_url, page)
    assert rendered.form.action == apply_url
    assert 'id="job-description"' in rendered.html
    assert "<form " in rendered.html
    assert "POSITION_DATA" not in rendered.html
    assert "window.__" not in rendered.html

    store = MemoryStore()
    store.save_path("Applicant/applicant_profile.json", json.dumps({
        "personal": {"name": "Fixture Applicant", "email": "fixture@example.test"},
        "skills": ["Python", "PostgreSQL", "Kafka"],
    }).encode("utf-8"))
    store.save_path("Applicant/resume.pdf", resume_pdf())
    document = MemoryDocument(posting_url)
    queue = GDTQ(document)
    index = WorkIndex(Path(tmp_path) / "index.db")
    runtime = {
        "queue": queue,
        "store": store,
        "index": index,
        "loader": ApplicationContextLoader(queue, store),
    }
    handlers = build_handlers(
        runtime,
        writer=cover_letter,
        resume_path="Applicant/resume.pdf",
        reader=PostingReader(site, renderer),
        submission_strategies=(PreparedFormSubmission(site, renderer, store),),
    )
    orchestrator = WorkloadOrchestrator(queue, StateTransitionEngine(), index, lambda *_: None, store=store)

    def dispatch(state, claim):
        payload = handlers[state](claim)
        payload["attempt_id"] = claim.attempt_id
        orchestrator.handle_outcome(state, payload)

    orchestrator.dispatch_agent = dispatch
    for _ in range(8):
        orchestrator.dispatch_tick()
    try:
        return site, index.events("item-1"), queue_name(queue, "item-1")
    finally:
        index.close()


@pytest.mark.parametrize("board", list(BOARDS))
def test_posting_url_reaches_confirmed_submission(tmp_path, board):
    build, apply_url = BOARDS[board]
    posting_url, page, apply_url = build(apply_url)
    site, events, state = run_pipeline(tmp_path, posting_url, page, apply_url, THANK_YOU + ". Reference FIT-1")
    assert state == "Applied"
    codes = [event["outcome"]["code"] for event in events]
    assert codes == ["BOOKMARKED", "MATCH", "PREPARED", "SUBMITTED"]
    posts = site.posts()
    assert len(posts) == 1
    assert posts[0].url == apply_url
    fields = site.form_fields(posts[0])
    assert fields["posting_url"] == posting_url
    assert fields["role"] == "Senior Backend Engineer"
    assert len(fields["package_sha256"]) == 64
    assert {item.url for item in site.exchanges} == {posting_url, apply_url}


def test_missing_thank_you_is_not_retried(tmp_path):
    build, apply_url = BOARDS["comeet"]
    posting_url, page, apply_url = build(apply_url)
    site, events, state = run_pipeline(tmp_path, posting_url, page, apply_url, "still processing")
    assert state == "Manual Apply Required"
    assert [event["outcome"]["code"] for event in events][-1] == "SUBMISSION_UNCONFIRMED"
    assert len(site.posts()) == 1


def test_unknown_page_script_is_refused():
    with pytest.raises(ValueError, match="no known job-board script"):
        PageRenderer().render("https://jobs.example.test/1", "<html><body>Careers</body></html>")
