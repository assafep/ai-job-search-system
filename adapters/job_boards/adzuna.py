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

from .interface import JobBoardAdapter, UnsupportedOperation


BASE_URL = "https://api.adzuna.com/v1/api/jobs"


def _required(environ, name):
    value = environ.get(name, "").strip()
    if not value:
        raise ValueError("{} is required for Adzuna discovery".format(name))
    return value


class AdzunaJobBoard(JobBoardAdapter):
    provider_name = 'adzuna'
    hosts = ('www.adzuna.com',)

    def search_api(self, context, transport):
        return self.search(what=context.query, where=' OR '.join(context.locations),
                           country=next(iter(context.countries), self.country).lower(),
                           page=context.page, results_per_page=context.limit, _transport=transport)

    def discovery_pages(self, context):
        country = next(iter(context.countries), self.country).lower()
        if country != 'gb':
            raise UnsupportedOperation('No verified public Adzuna search URL for this country')
        yield 'https://www.adzuna.co.uk/search?' + urllib.parse.urlencode({
            'q': context.query, 'w': ' OR '.join(context.locations)}), ''

    def __init__(self, app_id=None, app_key=None, country="il", opener=None):
        self.app_id = app_id if app_id is not None else os.environ.get("ADZUNA_APP_ID", "")
        self.app_key = app_key if app_key is not None else os.environ.get("ADZUNA_APP_KEY", "")
        self.country = country.strip().lower()
        if not self.country:
            raise ValueError("Adzuna country is required")
        self.opener = opener or urllib.request.urlopen

    def search(self, what="", where="", page=1, results_per_page=20, *, _transport=None, country=None, **filters):
        if page < 1 or results_per_page < 1:
            raise ValueError("page and results_per_page must be positive")
        from .interface import MissingCredentials
        if not self.app_id or not self.app_key:
            raise MissingCredentials("Adzuna API credentials are missing")
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
            urllib.parse.quote(country or self.country, safe=""),
            page,
            urllib.parse.urlencode(params),
        )
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with self._open(request, _transport) as response:
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
