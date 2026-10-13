import json

from adapters.job_boards.freehire import FreehireJobBoard


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def test_freehire_search_needs_no_credentials_and_normalizes():
    requests = []

    def opener(request):
        requests.append(request)
        return Response({"data": [{
            "public_slug": "backend-example-1",
            "title": "Backend Engineer",
            "company": "Example",
            "url": "https://freehire.me/jobs/backend-example-1",
            "apply_url": "https://boards.greenhouse.io/example/jobs/1",
            "location": "Tel Aviv, Israel",
            "description": "Build APIs",
            "countries": ["IL"],
            "skills": ["python"],
            "posted_at": "2026-10-13T10:00:00Z",
        }]})

    jobs = FreehireJobBoard(opener=opener).search(countries=["IL"])
    assert jobs[0]["company"] == "Example"
    assert jobs[0]["posting_url"].startswith("https://boards.greenhouse.io")
    assert "countries=IL" in requests[0].full_url
