import json

from adapters.job_boards.jobicy import JobicyJobBoard


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def test_jobicy_public_search_requires_no_credentials_and_normalizes():
    requests = []

    def opener(request):
        requests.append(request)
        return Response({"jobs": [{
            "id": 7,
            "jobTitle": "Backend Engineer",
            "companyName": "Example",
            "url": "https://jobicy.com/jobs/7",
            "jobGeo": "Anywhere",
            "jobExcerpt": "Build APIs",
            "jobIndustry": ["Engineering"],
            "jobType": ["Full-Time"],
        }]})

    jobs = JobicyJobBoard(opener=opener).search("python")
    assert jobs[0]["company"] == "Example"
    assert jobs[0]["posting_url"] == "https://jobicy.com/jobs/7"
    assert jobs[0]["source"] == "jobicy"
    assert "tag=python" in requests[0].full_url
