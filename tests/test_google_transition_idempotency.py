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
    repository.append_event = lambda application_id, event_type, data=None, idempotency_key=None: calls.append(
        (application_id, event_type, idempotency_key))

    # State-tab lookup is only used for the move request.
    monkeypatch.setattr(
        "persistence_api.google_docs_repository.state_tabs",
        lambda document: {"Interviewing": {"tabProperties": {"tabId": "state-interviewing"}}},
    )
    result = repository.transition("app-1", "Interviewing", idempotency_key="transition-1")

    assert result["status"] == "Interviewing"
    assert ("app-1", "status_changed", "transition-1") in calls
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
    repository.append_event = lambda application_id, event_type, data=None, idempotency_key=None: calls.append(
        (application_id, event_type, idempotency_key))

    assert repository.transition("app-1", "Interviewing", idempotency_key="transition-retry") == result
    assert ("app-1", "status_changed", "transition-retry") in calls
    assert repository._idempotency["transition-retry"] == result
