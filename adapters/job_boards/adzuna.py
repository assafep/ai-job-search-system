"""Read-only Adzuna job-search adapter.

Adzuna is a broad job-search source, so discovery does not require a list of
companies. Credentials are used only for GET requests; this adapter never
submits applications or mutates persistence state.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://api.adzuna.com/v1/api/jobs"


def _required(environ, name):
    value = environ.get(name, "").strip()
    if not value:
        raise ValueError("{} is required for Adzuna discovery".format(name))
    return value


class AdzunaJobBoard:
    def __init__(self, app_id, app_key, country="il", opener=None):
        self.app_id = app_id
        self.app_key = app_key
        self.country = country.strip().lower()
        if not self.country:
            raise ValueError("Adzuna country is required")
        self.opener = opener or urllib.request.urlopen

    def search(self, what="", where="", page=1, results_per_page=20, **filters):
        if page < 1 or results_per_page < 1:
            raise ValueError("page and results_per_page must be positive")
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": what or "",
            "where": where or "",
            "page": page,
            "results_per_page": results_per_page,
            "content-type": "application/json",
        }
        for name in ("sort_by", "full_time", "part_time", "contract", "permanent", "salary_min"):
            if filters.get(name) is not None:
                params[name] = filters[name]
        url = "{}/{}/search/{}?{}".format(
            BASE_URL,
            urllib.parse.quote(self.country, safe=""),
            page,
            urllib.parse.urlencode(params),
        )
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with self.opener(request) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RuntimeError("Adzuna returned HTTP {}".format(error.code))
        except urllib.error.URLError as error:
            raise RuntimeError("Adzuna unavailable: {}".format(error.reason))
        if not isinstance(payload, dict):
            raise RuntimeError("Adzuna returned an invalid response")
        return [self._normalize(item) for item in payload.get("results", [])]

    @staticmethod
    def _normalize(job):
        company = job.get("company") or {}
        location = job.get("location") or {}
        category = job.get("category") or {}
        return {
            "company": company.get("display_name", ""),
            "role": job.get("title", ""),
            "posting_url": job.get("redirect_url", ""),
            "location": location.get("display_name", ""),
            "description": job.get("description", ""),
            "category": category.get("label", ""),
            "source": "adzuna",
            "source_id": str(job.get("id", "")),
            "updated_at": job.get("created"),
            "contract_type": job.get("contract_type"),
            "contract_time": job.get("contract_time"),
            "salary_min": job.get("salary_min"),
            "salary_max": job.get("salary_max"),
        }


def client_from_environment(environ=None, opener=None):
    environ = os.environ if environ is None else environ
    return AdzunaJobBoard(
        _required(environ, "ADZUNA_APP_ID"),
        _required(environ, "ADZUNA_APP_KEY"),
        country=environ.get("ADZUNA_COUNTRY", "il"),
        opener=opener,
    )
