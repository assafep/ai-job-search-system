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
