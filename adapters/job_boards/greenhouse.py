"""Public Greenhouse Job Board API adapter.

Greenhouse board endpoints are public and do not require an API key. The
adapter only reads postings; application submission remains outside this
module and behind the workflow approval policy.
"""

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


def _plain_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html.unescape(value).split())


def boards_from_environment(environ=None):
    environ = os.environ if environ is None else environ
    raw = environ.get("GREENHOUSE_BOARDS", "")
    if not raw.strip():
        return {}
    try:
        boards = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("GREENHOUSE_BOARDS must be a JSON object of token to company") from error
    if not isinstance(boards, dict) or any(
        not isinstance(token, str) or not token.strip()
        or not isinstance(company, str) or not company.strip()
        for token, company in boards.items()
    ):
        raise ValueError("GREENHOUSE_BOARDS must map non-empty board tokens to company names")
    return {token.strip(): company.strip() for token, company in boards.items()}


class GreenhouseJobBoard:
    def __init__(self, boards, opener=None):
        if not isinstance(boards, dict) or not boards:
            raise ValueError("at least one Greenhouse board token is required")
        self.boards = boards
        self.opener = opener or urllib.request.urlopen

    def list_jobs(self):
        postings = []
        for token, company in self.boards.items():
            query = urllib.parse.urlencode({"content": "true"})
            url = "{}/{}/jobs?{}".format(BASE_URL, urllib.parse.quote(token, safe=""), query)
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                with self.opener(request) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                raise RuntimeError("Greenhouse board {} returned HTTP {}".format(token, error.code))
            except urllib.error.URLError as error:
                raise RuntimeError("Greenhouse board {} unavailable: {}".format(token, error.reason))
            for job in payload.get("jobs", []):
                postings.append(self._normalize(company, job))
        return postings

    @staticmethod
    def _normalize(company, job):
        departments = [item.get("name") for item in job.get("departments", []) if item.get("name")]
        offices = [item.get("name") for item in job.get("offices", []) if item.get("name")]
        return {
            "company": company,
            "role": job.get("title", ""),
            "posting_url": job.get("absolute_url", ""),
            "location": (job.get("location") or {}).get("name", ""),
            "description": _plain_text(job.get("content", "")),
            "departments": departments,
            "offices": offices,
            "source": "greenhouse",
            "source_id": str(job.get("id", "")),
            "updated_at": job.get("updated_at"),
        }


def client_from_environment(environ=None, opener=None):
    return GreenhouseJobBoard(boards_from_environment(environ), opener=opener)
