import json

import pytest

from adapters.job_boards.adzuna import AdzunaJobBoard, client_from_environment


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def test_adzuna_search_normalizes_broad_results():
    requests = []

    def opener(request):
        requests.append(request)
        return Response({"results": [{
            "id": 42,
            "title": "Senior Engineer",
            "redirect_url": "https://jobs.example/42",
            "description": "Build things",
            "company": {"display_name": "Example"},
            "location": {"display_name": "Tel Aviv"},
            "category": {"label": "IT Jobs"},
            "created": "2026-10-13T10:00:00Z",
        }]})

    jobs = AdzunaJobBoard("id", "key", opener=opener).search("engineer", "Tel Aviv")
    assert jobs[0]["company"] == "Example"
    assert jobs[0]["posting_url"] == "https://jobs.example/42"
    assert jobs[0]["source_id"] == "42"
    assert "app_id=id" in requests[0].full_url
    assert "/il/search/1" in requests[0].full_url


def test_adzuna_credentials_are_required():
    with pytest.raises(ValueError, match="ADZUNA_APP_ID"):
        client_from_environment({"ADZUNA_APP_KEY": "key"})
