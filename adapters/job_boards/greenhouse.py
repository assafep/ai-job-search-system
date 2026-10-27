"""Public Greenhouse Job Board API adapter.

Greenhouse board endpoints are public and do not require an API key. The
adapter only reads postings; application submission remains outside this
module and behind the stage approval policy.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .interface import EmployerJobBoard, UnsupportedOperation
from .parsing import plain_text as _plain_text


BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


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


class GreenhouseJobBoard(EmployerJobBoard):
    provider_name = 'greenhouse'
    hosts = ('boards.greenhouse.io', 'job-boards.greenhouse.io')

    def discovery_pages(self, context):
        for token, company in (context.boards or self.boards).items():
            yield 'https://boards.greenhouse.io/' + urllib.parse.quote(token, safe=''), company

    def fetch_details_api(self, context, transport):
        from .parsing import text
        match = re.fullmatch(r'/([^/]+)/jobs/(\d+)/?', urllib.parse.urlsplit(context.posting_url).path)
        if not match:
            raise UnsupportedOperation('Not an exact Greenhouse posting URL')
        url = BASE_URL + '/{}/jobs/{}'.format(*match.groups())
        payload = transport.read_json(url)
        if payload.get('id') is not None and str(payload['id']) != match[2]:
            raise ValueError('Greenhouse API returned a different posting')
        return transport.details(context.posting_url, url, text(payload.get('content') or ''))

    def __init__(self, boards=None, opener=None, timeout=30):
        if boards is not None and (not isinstance(boards, dict) or not boards):
            raise ValueError("at least one Greenhouse board token is required")
        self.boards = boards or {}
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def list_jobs(self, *, boards=None, _transport=None):
        postings = []
        for token, company in (boards if boards is not None else self.boards).items():
            query = urllib.parse.urlencode({"content": "true"})
            url = "{}/{}/jobs?{}".format(BASE_URL, urllib.parse.quote(token, safe=""), query)
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            try:
                # Custom openers retain the small test/adapter interface.
                response_context = self._open(request, _transport)
                with response_context as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                raise RuntimeError("Greenhouse board {} returned HTTP {}".format(token, error.code))
            except urllib.error.URLError as error:
                raise RuntimeError("Greenhouse board {} unavailable: {}".format(token, error.reason))
            if not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list):
                raise RuntimeError('Greenhouse board {} returned an invalid response'.format(token))
            for job in payload['jobs']:
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
            "posted_at": job.get("first_published"),
            "publication_date_kind": "first_published",
        }


def client_from_environment(environ=None, opener=None):
    return GreenhouseJobBoard(boards_from_environment(environ), opener=opener)
