from persistence_api.google_docs_repository import (
    GoogleDocsRepository,
    application_tab_title,
    document_id_from_url,
)


def test_application_tab_title_respects_google_docs_limit():
    title = application_tab_title("A Very Long Company Name", "An Extremely Long Senior Engineering Role")
    assert len(title) <= 50
    assert title.startswith("A Very Long Company Name / An Extremely")


def test_document_id_accepts_standard_and_user_scoped_google_docs_urls():
    assert document_id_from_url("https://docs.google.com/document/d/journal-123/edit") == "journal-123"
    assert document_id_from_url("https://docs.google.com/document/u/0/d/journal-123/edit") == "journal-123"
    assert document_id_from_url("journal-123") == "journal-123"


def tab(tab_id, title, parent=None, paragraphs=None, children=None):
    content = []
    index = 1
    for text, listed in paragraphs or []:
        content.append({
            "startIndex": index,
            "endIndex": index + len(text) + 1,
            "paragraph": {
                "elements": [{"textRun": {"content": text + "\n"}}],
                **({"bullet": {"listId": "test-list"}} if listed else {}),
            },
        })
        index += len(text) + 1
    return {
        "tabProperties": {
            "tabId": tab_id,
            "title": title,
            "parentTabId": parent,
        },
        "documentTab": {"body": {"content": content}},
        "childTabs": children or [],
    }


class FakeClient:
    def __init__(self, document):
        self.document = document

    def get_document(self):
        return self.document

    def batch_update(self, requests, revision_id=None):
        """Minimal batch_update for creating top-level optional state tabs."""
        for request in requests:
            add = request.get("addDocumentTab")
            if add:
                props = add["tabProperties"]
                parent_id = props.get("parentTabId")
                created = tab("fake-" + props["title"].replace(" ", "-"), props["title"], parent=parent_id)
                if parent_id:
                    for root in self.document.get("tabs", []):
                        if root["tabProperties"]["tabId"] == parent_id:
                            root.setdefault("childTabs", []).append(created)
                else:
                    self.document.setdefault("tabs", []).append(created)
        return {}


class FakeResearchClient(FakeClient):
    def __init__(self, document):
        super().__init__(document)
        self.requests = []
        self.next_id = "research-1"

    def batch_update(self, requests, revision_id=None):
        self.requests.extend(requests)
        for request in requests:
            add = request.get("addDocumentTab")
            if add:
                props = add["tabProperties"]
                research = tab(self.next_id, props["title"], parent=props["parentTabId"])
                for root in self.document["tabs"]:
                    for application in root.get("childTabs", []):
                        if application["tabProperties"]["tabId"] == props["parentTabId"]:
                            application.setdefault("childTabs", []).append(research)
        return {}


class FakeCreateClient(FakeClient):
    def __init__(self, document):
        super().__init__(document)
        self.next_id = "app-new"

    def batch_update(self, requests, revision_id=None):
        for request in requests:
            add = request.get("addDocumentTab")
            if not add:
                continue
            props = add["tabProperties"]
            parent_id = props.get("parentTabId")
            created = tab(self.next_id, props["title"], parent=parent_id)
            if parent_id:
                for root in self.document["tabs"]:
                    if root["tabProperties"]["tabId"] == parent_id:
                        root.setdefault("childTabs", []).append(created)
            else:
                # Top-level tab (e.g., optional state tab)
                self.document["tabs"].append(created)
        return {}


class CaptureClient(FakeClient):
    def __init__(self, document):
        super().__init__(document)
        self.requests = []

    def batch_update(self, requests, revision_id=None):
        self.requests.extend(requests)
        return {}


def test_repository_reads_state_tabs_and_application_journal():
    bookmarked = tab("state-bookmarked", "Bookmarked")
    application = tab(
        "app-1",
        "Example / Senior Backend Engineer",
        parent="state-bookmarked",
        paragraphs=[
            ("2026-10-12", False),
            ("Posting URL: https://example.test/job", True),
            ("Added by Job Scout", True),
            ("13/10/2026", False),
            ("Phone call with recruiter", True),
        ],
    )
    bookmarked["childTabs"] = [application]
    states = [bookmarked]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    repository = GoogleDocsRepository("token", "document")
    repository.client = FakeClient({"revisionId": "rev-1", "tabs": states})

    applications = repository.list_applications()
    assert len(applications) == 1
    assert applications[0].application_id == "app-1"
    assert applications[0].status == "Bookmarked"
    assert applications[0].posting_url == "https://example.test/job"
    assert applications[0].created_at == "2026-10-12T00:00:00+00:00"
    assert len(repository.list_events("app-1")) == 2
    assert repository.list_events("app-1")[0].occurred_at == "2026-10-12T00:00:00+00:00"


def test_completed_posting_artifacts_remain_readable_for_duplicate_checks():
    import pytest
    from adapters.job_boards.posting_details import posting_content_bytes, read_posting_artifact
    from persistence_api.journal_format import STATE_TABS
    from services.orchestrator import Orchestrator
    from services.workload_registry import WorkloadRegistry

    repository = GoogleDocsRepository("token", "active", completed_document_id="completed")
    repository.client = FakeClient({"tabs": [tab("state-" + name, name) for name in STATE_TABS]})
    rejected = tab("rejected", "Rejected", children=[tab(
        "completed-app", "Lemonade / Backend Engineer", parent="rejected",
        paragraphs=[("2026-10-18", False),
                    ("Posting URL: https://example.test/lemonade", True),
                    ("Artifact: Job Posting - Lemonade.json (Drive file: posting-file)", True)])])
    repository.completed_client = FakeClient({"tabs": [rejected]})
    description = "Archived backend posting requirements. " * 30

    class Artifacts:
        def list_application_files(self, company, role):
            assert (company, role) == ("Lemonade", "Backend Engineer")
            return [{"id": "posting-file", "name": "Job Posting - Lemonade.json",
                     "mimeType": "application/json"}]

        def read(self, file_id):
            assert file_id == "posting-file"
            return posting_content_bytes({"posting_url": "https://example.test/lemonade",
                                          "description": description})

    repository.artifacts = Artifacts()
    artifacts = repository.list_artifacts("completed-app")
    assert len(artifacts) == 1
    assert artifacts[0].application_id == "completed-app"
    assert read_posting_artifact(repository, "completed-app", "https://example.test/lemonade")["description"] == description
    with pytest.raises(KeyError):
        repository.list_artifacts("missing-app")

    orchestrator = Orchestrator(repository, WorkloadRegistry(), agents=None)
    try:
        sentra = {"application_id": "sentra", "company": "Sentra", "role": "Backend Engineer",
                  "posting_url": "https://example.test/sentra"}
        assert orchestrator._handled_match(sentra, "Different data team requirements. " * 30) is None
        match, reason = orchestrator._handled_match(sentra, description)
        assert match["application_id"] == "completed-app"
        assert reason == "posting content"
    finally:
        orchestrator.close()


def test_repository_rejects_journal_without_offer_state_tab():
    states = [tab("state-" + name.replace(" ", "-"), name) for name in (
        "Bookmarked", "Prepared", "Manual Apply Required", "Dead Letters Queue", "Applied", "Interviewing", "Rejected"
    )]
    repository = GoogleDocsRepository("token", "document")
    repository.client = FakeClient({"tabs": states})

    try:
        repository.list_applications()
    except ValueError as error:
        assert str(error) == (
            "journal is missing top-level state tabs: Offer. Required tabs: "
            "Bookmarked, Prepared, Manual Apply Required, Applied, Interviewing, Rejected, Offer"
        )
    else:
        raise AssertionError("a journal without Offer must be rejected")


def test_repair_journal_adds_missing_top_level_state_tab():
    states = [tab("state-" + name.replace(" ", "-"), name) for name in (
        "Bookmarked", "Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected"
    )]

    class RepairClient(FakeClient):
        def __init__(self, document):
            super().__init__(document)
            self.requests = []

        def batch_update(self, requests, revision_id=None):
            self.requests.extend(requests)
            for request in requests:
                properties = request["addDocumentTab"]["tabProperties"]
                self.document["tabs"].append(tab("state-offer", properties["title"]))

    client = RepairClient({"revisionId": "rev-1", "tabs": states})
    repository = GoogleDocsRepository("token", "document")
    repository.client = client

    result = repository.repair_journal()

    assert result["valid"] is True
    assert result["missing_states"] == []
    assert client.requests == [{"addDocumentTab": {"tabProperties": {"title": "Offer"}}}]


def test_journal_status_reports_duplicate_top_level_state_tab():
    states = [tab("state-" + name.replace(" ", "-"), name) for name in (
        "Bookmarked", "Prepared", "Manual Apply Required", "Dead Letters Queue", "Applied", "Interviewing", "Rejected", "Offer"
    )]
    states.append(tab("state-bookmarked-duplicate", "Bookmarked"))
    repository = GoogleDocsRepository("token", "document")
    repository.client = FakeClient({"tabs": states})

    result = repository.journal_status()

    assert result["valid"] is False
    assert result["missing_states"] == []
    assert result["duplicate_states"] == ["Bookmarked"]
    try:
        repository.list_applications()
    except ValueError as error:
        assert str(error) == "journal has duplicate top-level state tabs: Bookmarked"
    else:
        raise AssertionError("duplicate state tabs must be rejected")


def test_create_application_uses_new_tab_when_title_is_not_unique():
    bookmarked = tab("state-bookmarked", "Bookmarked")
    old = tab(
        "app-old",
        "Example / Backend",
        parent="state-bookmarked",
        paragraphs=[("2026-10-12", False), ("Posting URL: https://example.test/old", True)],
    )
    bookmarked["childTabs"] = [old]
    states = [bookmarked]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    client = FakeCreateClient({"revisionId": "rev-1", "tabs": states})
    repository = GoogleDocsRepository("token", "document")
    repository.client = client
    appended = []
    repository.append_event = lambda application_id, event_type, data=None, idempotency_key=None: appended.append(application_id)

    created = repository.create_application("Example", "Backend", "https://example.test/new")

    assert created.application_id == "app-new"
    assert appended == ["app-new"]


def test_keyed_create_recovers_matching_empty_tab_after_interrupted_write():
    bookmarked = tab("state-bookmarked", "Bookmarked")
    unfinished = tab("app-interrupted", "Example / Backend", parent="state-bookmarked")
    bookmarked["childTabs"] = [unfinished]
    states = [bookmarked]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    repository = GoogleDocsRepository("token", "document")
    repository.client = FakeClient({"revisionId": "rev-1", "tabs": states})
    appended = []
    repository.append_event = lambda application_id, event_type, data=None, idempotency_key=None: appended.append(
        (application_id, event_type, idempotency_key)
    )
    repository.get_application = lambda application_id: {
        "application_id": application_id, "status": "Bookmarked"
    }

    result = repository.create_application(
        "Example", "Backend", "https://example.test/interrupted", idempotency_key="create-1"
    )

    assert result["application_id"] == "app-interrupted"
    assert appended == [("app-interrupted", "application_created", "create-1")]


def test_repository_reads_flattened_tabs_and_rendered_bullet_glyphs():
    state = tab("state-bookmarked", "Bookmarked")
    application = tab(
        "app-1",
        "Example / Backend",
        parent="state-bookmarked",
        paragraphs=[
            ("2026-10-12", False),
            ("- Posting URL: https://example.test/job", True),
            ("- Added by Job Scout", True),
        ],
    )
    states = [state, application]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    repository = GoogleDocsRepository("token", "document")
    repository.client = FakeClient({"tabs": states})

    applications = repository.list_applications()

    assert len(applications) == 1
    assert applications[0].posting_url == "https://example.test/job"
    assert repository.list_events("app-1")[0].data["bullets"] == [
        "Posting URL: https://example.test/job",
        "Added by Job Scout",
    ]


def test_repository_reads_connected_flattened_tab_shape():
    def flat(tab_id, title, parent=None, paragraphs=None):
        content = []
        index = 1
        for text, listed in paragraphs or []:
            content.append({
                "startIndex": index,
                "endIndex": index + len(text) + 1,
                "paragraph": {
                    "elements": [{"textRun": {"content": text + "\n"}}],
                    **({"bullet": {"listId": "list-1"}} if listed else {}),
                },
            })
            index += len(text) + 1
        return {
            "tabId": tab_id,
            "title": title,
            "parentTabId": parent,
            "body": {"content": content},
        }

    states = [flat("state-bookmarked", "Bookmarked")]
    states.append(flat(
        "app-1",
        "Example / Backend",
        "state-bookmarked",
        [("2026-10-12", False), ("Posting URL: https://example.test/job", True)],
    ))
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(flat("state-" + name.replace(" ", "-"), name))

    repository = GoogleDocsRepository("token", "document")
    repository.client = FakeClient({"tabs": states})

    applications = repository.list_applications()
    assert len(applications) == 1
    assert applications[0].application_id == "app-1"
    assert applications[0].posting_url == "https://example.test/job"


def test_repository_finds_legacy_posting_url_before_first_date():
    state = tab("state-bookmarked", "Bookmarked")
    application = tab(
        "app-1",
        "Example / Backend",
        parent="state-bookmarked",
        paragraphs=[
            ("https://example.test/legacy", False),
            ("2026-10-12", False),
            ("Applied via legacy workflow", True),
        ],
    )
    state["childTabs"] = [application]
    states = [state]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    repository = GoogleDocsRepository("token", "document")
    repository.client = FakeClient({"tabs": states})

    assert repository.get_application("app-1").posting_url == "https://example.test/legacy"


def test_appended_url_is_written_as_a_native_hyperlink():
    bookmarked = tab("state-bookmarked", "Bookmarked")
    application = tab("app-1", "Example / Backend", parent="state-bookmarked")
    bookmarked["childTabs"] = [application]
    states = [bookmarked]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    client = CaptureClient({"revisionId": "rev-1", "tabs": states})
    repository = GoogleDocsRepository("token", "document")
    repository.client = client

    repository.append_event("app-1", "note", {
        "date": "2026-10-12",
        "bullets": ["Posting URL: https://example.test/job"],
    })

    link_requests = [request["updateTextStyle"] for request in client.requests if "updateTextStyle" in request]
    assert len(link_requests) == 1
    assert link_requests[0]["textStyle"]["link"]["url"] == "https://example.test/job"


def test_appended_event_has_separator_and_bullets_exclude_trailing_blank_line():
    bookmarked = tab("state-bookmarked", "Bookmarked")
    application = tab(
        "app-1", "Example / Backend", parent="state-bookmarked",
        paragraphs=[("2026-10-12", False), ("Previous event", True)],
    )
    bookmarked["childTabs"] = [application]
    states = [bookmarked]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    client = CaptureClient({"revisionId": "rev-1", "tabs": states})
    repository = GoogleDocsRepository("token", "document")
    repository.client = client

    repository.append_event("app-1", "note", {
        "date": "2026-10-13", "bullets": ["New event"],
    })

    insert = next(request["insertText"] for request in client.requests if "insertText" in request)
    assert insert["text"].startswith("\n\n2026-10-13\n\nNew event\n\n")
    bullets = next(request["createParagraphBullets"] for request in client.requests if "createParagraphBullets" in request)
    assert bullets["range"]["endIndex"] < insert["location"]["index"] + len(insert["text"])


def test_repair_application_journal_rebuilds_clean_ranges():
    bookmarked = tab("state-bookmarked", "Bookmarked")
    application = tab(
        "app-1", "Example / Backend", parent="state-bookmarked",
        paragraphs=[
            ("2026-10-12", False),
            ("Added by Job Scout2026-10-13", True),
            ("Moved from Bookmarked to Prepared", True),
        ],
    )
    bookmarked["childTabs"] = [application]
    states = [bookmarked]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    client = CaptureClient({"revisionId": "rev-1", "tabs": states})
    repository = GoogleDocsRepository("token", "document")
    repository.client = client

    result = repository.repair_application_journal("app-1")

    assert result == {"application_id": "app-1", "events_repaired": 2}
    inserts = [request["insertText"] for request in client.requests if "insertText" in request]
    assert inserts[0]["text"] == (
        "2026-10-12\n\nAdded by Job Scout\n\n"
        "2026-10-13\n\nMoved from Bookmarked to Prepared\n\n"
    )


def test_google_event_rejects_non_date_paragraph():
    bookmarked = tab("state-bookmarked", "Bookmarked")
    application = tab("app-1", "Example / Backend", parent="state-bookmarked")
    bookmarked["childTabs"] = [application]
    states = [bookmarked]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    repository = GoogleDocsRepository("token", "document")
    repository.client = CaptureClient({"revisionId": "rev-1", "tabs": states})

    try:
        repository.append_event("app-1", "note", {"date": "tomorrow", "bullets": ["Not valid"]})
    except ValueError as error:
        assert "journal event date" in str(error)
    else:
        raise AssertionError("invalid journal dates should be rejected")


def test_repository_writes_nested_research_tab():
    bookmarked = tab("state-bookmarked", "Bookmarked")
    application = tab("app-1", "Example / Senior Backend Engineer", parent="state-bookmarked")
    bookmarked["childTabs"] = [application]
    states = [bookmarked]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    client = FakeResearchClient({"revisionId": "rev-1", "tabs": states})
    repository = GoogleDocsRepository("token", "document")
    repository.client = client

    result = repository.write_research("app-1", "Research — Interview Briefing", "Ask about scale", "research-try-1")

    assert result["research_tab_id"] == "research-1"
    assert client.requests[0]["addDocumentTab"]["tabProperties"]["parentTabId"] == "app-1"
    assert client.requests[1]["insertText"]["location"]["tabId"] == "research-1"


def test_repository_reads_nested_research_tabs():
    bookmarked = tab("state-bookmarked", "Bookmarked")
    research = tab(
        "research-1",
        "Research — Interview Briefing",
        parent="app-1",
        paragraphs=[("Company overview", False), ("Ask about scale", True)],
    )
    application = tab("app-1", "Example / Backend", parent="state-bookmarked", children=[research])
    bookmarked["childTabs"] = [application]
    states = [bookmarked]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    repository = GoogleDocsRepository("token", "document")
    repository.client = FakeClient({"tabs": states})

    assert repository.list_research("app-1") == [{
        "application_id": "app-1",
        "title": "Research — Interview Briefing",
        "research_tab_id": "research-1",
        "content": "Company overview\nAsk about scale",
    }]


def test_repository_reads_flattened_research_tabs():
    state = tab("state-bookmarked", "Bookmarked")
    application = tab("app-1", "Example / Backend", parent="state-bookmarked")
    research = tab(
        "research-1",
        "Research — Interview Briefing",
        parent="app-1",
        paragraphs=[("Company overview", False), ("Ask about scale", True)],
    )
    states = [state, application, research]
    for name in ("Prepared", "Manual Apply Required", "Applied", "Interviewing", "Rejected", "Offer"):
        states.append(tab("state-" + name.replace(" ", "-"), name))
    repository = GoogleDocsRepository("token", "document")
    repository.client = FakeClient({"tabs": states})

    assert repository.list_research("app-1") == [{
        "application_id": "app-1",
        "title": "Research — Interview Briefing",
        "research_tab_id": "research-1",
        "content": "Company overview\nAsk about scale",
    }]


def test_google_journal_idempotency_marker_survives_process_restart():
    application = tab(
        "app-1",
        "Example / Backend",
        parent="state-bookmarked",
        paragraphs=[
            ("2026-10-12", False),
            ("Added by workflow", True),
            ("Operation idempotency key: operation-1", True),
        ],
    )
    repository = GoogleDocsRepository("token", "document")

    event = repository._idempotent_event(application, "app-1", "operation-1")

    assert event is not None
    assert event.data["bullets"] == ["Added by workflow"]


def test_store_artifact_does_not_duplicate_journal_reference_after_retry(monkeypatch):
    repository = GoogleDocsRepository("token", "document")
    application = type("Application", (), {
        "company": "Example",
        "role": "Backend",
    })()
    repository.get_application = lambda application_id: application
    repository.artifacts = type("Store", (), {
        "save": lambda self, *args: {"id": "drive-1", "mimeType": "application/pdf"},
    })()
    existing = type("Artifact", (), {"drive_file_id": "drive-1"})()
    repository.list_artifacts = lambda application_id: [existing]
    repository.save_artifact = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("journal should not be appended twice"))

    assert repository.store_artifact("app-1", "application.pdf", b"pdf", "application/pdf", "try-1") is existing
