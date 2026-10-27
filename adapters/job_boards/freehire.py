"""Read-only Freehire job-search adapter.

Freehire's public search endpoints require no account or API key. The caller
may omit a query and use geography/facets to provide a broad candidate set for
the resume-aware discovery agent.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from .interface import JobBoardAdapter
from .parsing import plain_text as _plain_text


BASE_URL = "https://freehire.me/api/v1/jobs/search"


class FreehireJobBoard(JobBoardAdapter):
    provider_name = 'freehire'
    hosts = ('freehire.me',)

    def search_api(self, context, transport):
        return self.search(query=context.query, countries=context.countries, limit=context.limit,
                           offset=context.offset, _transport=transport)

    def discovery_pages(self, context):
        yield 'https://freehire.me/jobs?' + urllib.parse.urlencode({'q': context.query}), ''

    def __init__(self, opener=None):
        self.opener = opener or urllib.request.urlopen

    def search(self, query="", countries=None, limit=100, offset=0, *, _transport=None):
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        params = {
            "q": query,
            "description_format": "text",
            "limit": limit,
            "offset": offset,
            "sort": "posted_at",
            "order": "desc",
        }
        if countries:
            params["countries"] = ",".join(countries) if isinstance(countries, list) else countries
        request = urllib.request.Request(
            BASE_URL + "?" + urllib.parse.urlencode(params),
            headers={
                "Accept": "application/json",
                "User-Agent": "ai-job-search-system/1.0",
            },
        )
        try:
            with self._open(request, _transport) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RuntimeError("Freehire returned HTTP {}".format(error.code))
        except urllib.error.URLError as error:
            raise RuntimeError("Freehire unavailable: {}".format(error.reason))
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError("Freehire returned an invalid response")
        return [self._normalize(item) for item in payload["data"]]

    @staticmethod
    def _normalize(job):
        enrichment = job.get("enrichment") or {}
        return {
            "company": job.get("company", ""),
            "role": job.get("title", ""),
            "posting_url": job.get("apply_url") or job.get("url", ""),
            "listing_url": job.get("url", ""),
            "location": job.get("location", ""),
            "description": _plain_text(job.get("description", "")),
            "countries": job.get("countries") or [],
            "regions": job.get("regions") or [],
            "skills": job.get("skills") or [],
            "category": enrichment.get("category", ""),
            "source": "freehire",
            "source_id": job.get("public_slug", ""),
            "updated_at": job.get("updated_at") or job.get("posted_at"),
            "posted_at": job.get("posted_at"),
            "salary_min": enrichment.get("salary_min"),
            "salary_max": enrichment.get("salary_max"),
        }
