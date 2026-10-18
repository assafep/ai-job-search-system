from persistence_api.google_docs_repository import GoogleDocsRepository


def test_google_transition_passes_idempotency_key_to_status_event(monkeypatch):
    repository = GoogleDocsRepository("token", "document")
    calls = []

    repository._find_application = lambda document, application_id: (
        "Applied", {"tabProperties": {"tabId": "state-applied"}},
        {"tabProperties": {"tabId": application_id, "title": "Example / Backend"}},
    )
    repository.client.get_document = lambda: {"revisionId": "rev-1", "tabs": []}
    repository.client.batch_update = lambda requests, revision_id=None: calls.append((requests, revision_id))
    repository.get_application = lambda application_id: {"application_id": application_id, "status": "Interviewing"}

    # State-tab lookup is only used for the move request.
    monkeypatch.setattr(
        "persistence_api.google_docs_repository.state_tabs",
        lambda document: {"Interviewing": {"tabProperties": {"tabId": "state-interviewing"}}},
    )
    result = repository.transition("app-1", "Interviewing", idempotency_key="transition-1")

    assert result["status"] == "Interviewing"
    assert len(calls) == 1
    requests, revision = calls[0]
    assert revision == 'rev-1'
    assert requests[0]['updateDocumentTabProperties']['tabProperties']['parentTabId'] == 'state-interviewing'
    event_text = next(item['insertText']['text'] for item in requests if 'insertText' in item)
    assert 'Moved from Applied to Interviewing' in event_text
    assert 'Operation idempotency key: transition-1' in event_text
    assert repository._idempotency["transition-1"] == result


def test_google_transition_appends_event_when_move_already_committed(monkeypatch):
    repository = GoogleDocsRepository("token", "document")
    calls = []
    repository._find_application = lambda document, application_id: (
        "Interviewing", {"tabProperties": {"tabId": "state-interviewing"}},
        {"tabProperties": {"tabId": application_id, "title": "Example / Backend"}},
    )
    repository.client.get_document = lambda: {"revisionId": "rev-1", "tabs": []}
    result = {"application_id": "app-1", "status": "Interviewing"}
    repository.get_application = lambda application_id: result
    repository.client.batch_update = lambda requests, revision_id=None: calls.append(requests)

    assert repository.transition("app-1", "Interviewing", idempotency_key="transition-retry") == result
    assert len(calls) == 1
    assert not any('updateDocumentTabProperties' in item for item in calls[0])
    assert 'Operation idempotency key: transition-retry' in calls[0][0]['insertText']['text']
    assert repository._idempotency["transition-retry"] == result


def test_bookmark_staged_tab_commits_move_and_event_together(monkeypatch):
    repository = GoogleDocsRepository("token", "document")
    calls = []
    tab = {"tabProperties": {"tabId": "app-1", "title": "Example / Backend", "parentTabId": "state-replay"}}
    repository.client.get_document = lambda: {"revisionId": "rev-9", "tabs": []}
    repository.client.batch_update = lambda requests, revision_id=None: calls.append((requests, revision_id))
    result = {"application_id": "app-1", "status": "Bookmarked"}
    repository.get_application = lambda application_id: result
    monkeypatch.setattr(
        "persistence_api.google_docs_repository.state_tabs",
        lambda document: {
            "Bookmarked": {"tabProperties": {"tabId": "state-bookmarked"}},
            "Replay": {"tabProperties": {"tabId": "state-replay"}},
        },
    )
    monkeypatch.setattr("persistence_api.google_docs_repository.find_tab", lambda tabs, wanted: tab)
    monkeypatch.setattr(
        "persistence_api.google_docs_repository.document_tabs",
        lambda document: [{"tabProperties": {"tabId": "state-replay", "title": "Replay"}}, tab],
    )

    def children(document, parent):
        return [tab] if parent["tabProperties"]["tabId"] == "state-replay" else []

    monkeypatch.setattr("persistence_api.google_docs_repository.children_of", children)
    assert repository.bookmark_staged_tab("app-1", "bookmark-1") == result
    assert repository.bookmark_staged_tab("app-1", "bookmark-1") == result
    assert len(calls) == 1
    requests, revision = calls[0]
    assert revision == "rev-9"
    move = next(item["updateDocumentTabProperties"] for item in requests if "updateDocumentTabProperties" in item)
    assert move["tabProperties"]["parentTabId"] == "state-bookmarked"
    text = next(item["insertText"]["text"] for item in requests if "insertText" in item)
    assert "Position bookmarked from the operator Replay tab." in text
    assert "Operation idempotency key: bookmark-1" in text
