import json

from services.application_service_client import ApplicationServiceClient


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


def test_client_serializes_profile_and_idempotent_event_request():
    calls = []

    def opener(request):
        calls.append((request.method, request.full_url, json.loads(request.data.decode("utf-8"))))
        return FakeResponse({"ok": True})

    client = ApplicationServiceClient("http://persistence.test", opener=opener)
    assert client.save_profile({"schema_version": 1}) == {"ok": True}
    assert client.append_event("tab-1", "note", {"bullets": ["hello"]}, "event-1") == {"ok": True}
    assert calls == [
        ("PUT", "http://persistence.test/profile", {"schema_version": 1}),
        ("POST", "http://persistence.test/applications/tab-1/events", {
            "event_type": "note",
            "data": {"bullets": ["hello"]},
            "idempotency_key": "event-1",
        }),
    ]


def test_client_adds_optional_bearer_token():
    headers = []

    def opener(request):
        headers.append(request.get_header("Authorization"))
        return FakeResponse({"ok": True})

    client = ApplicationServiceClient("http://persistence.test", opener=opener, auth_token="secret")
    client.get_profile()

    assert headers == ["Bearer secret"]


def test_client_base64_encodes_artifact_content():
    calls = []

    def opener(request):
        calls.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse({"artifact_id": "artifact-1"})

    client = ApplicationServiceClient("http://persistence.test", opener=opener)
    assert client.store_artifact("tab-1", "resume.pdf", b"pdf", "application/pdf", "artifact-try-1") == {
        "artifact_id": "artifact-1"
    }
    assert calls[0]["name"] == "resume.pdf"
    assert calls[0]["content_base64"] == "cGRm"
    assert calls[0]["idempotency_key"] == "artifact-try-1"


def test_client_writes_research_tab():
    calls = []

    def opener(request):
        calls.append((request.method, request.full_url, json.loads(request.data.decode("utf-8"))))
        return FakeResponse({"research_tab_id": "research-1"})

    client = ApplicationServiceClient("http://persistence.test", opener=opener)
    assert client.write_research("tab-1", "Research — Interview Briefing", "Ask about scale", "research-1") == {
        "research_tab_id": "research-1"
    }
    assert calls[0][1] == "http://persistence.test/applications/tab-1/research"
    assert calls[0][2]["idempotency_key"] == "research-1"


def test_client_reads_research_tabs():
    calls = []

    def opener(request):
        calls.append((request.method, request.full_url))
        return FakeResponse([{"title": "Research — Interview Briefing"}])

    client = ApplicationServiceClient("http://persistence.test", opener=opener)
    assert client.list_research("tab-1") == [{"title": "Research — Interview Briefing"}]
    assert calls == [("GET", "http://persistence.test/applications/tab-1/research")]


def test_client_reads_and_writes_root_relative_files():
    calls = []

    def opener(request):
        calls.append((request.method, request.full_url))
        if request.method == "GET":
            return FakeResponse({"file": {"path": "shared/notes.md"}, "content_base64": "bm90ZXM="})
        return FakeResponse({"file_id": "file-1"})

    client = ApplicationServiceClient("http://persistence.test", opener=opener)
    assert client.store_file("shared/notes.md", b"notes", "text/markdown", "file-1") == {"file_id": "file-1"}
    assert client.read_file("shared/notes.md")["content"] == b"notes"
    assert calls[0] == ("POST", "http://persistence.test/files")
    assert calls[1][0] == "GET"
    assert "path=shared%2Fnotes.md" in calls[1][1]
