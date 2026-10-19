import json

from adapters.job_boards.greenhouse import GreenhouseJobBoard, boards_from_environment


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


def test_greenhouse_adapter_normalizes_public_postings():
    requests = []

    def opener(request):
        requests.append(request)
        return FakeResponse({"jobs": [{
            "id": 123,
            "title": "Senior Backend Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
            "location": {"name": "Tel Aviv, Israel"},
            "content": "<p>Build <strong>reliable</strong> services.</p>",
            "departments": [{"name": "Engineering"}],
            "offices": [{"name": "Tel Aviv"}],
            "updated_at": "2026-10-13T10:00:00Z",
            "first_published": "2026-10-01T10:00:00Z",
        }]})

    jobs = GreenhouseJobBoard({"acme": "Acme Corp"}, opener=opener).list_jobs()

    assert len(jobs) == 1
    assert jobs[0]["company"] == "Acme Corp"
    assert jobs[0]["role"] == "Senior Backend Engineer"
    assert jobs[0]["description"] == "Build reliable services."
    assert jobs[0]["source_id"] == "123"
    assert jobs[0]['posted_at'] == '2026-10-01T10:00:00Z'
    assert "content=true" in requests[0].full_url


def test_greenhouse_board_configuration_is_json():
    assert boards_from_environment({"GREENHOUSE_BOARDS": '{"acme":"Acme Corp"}'}) == {
        "acme": "Acme Corp"
    }
