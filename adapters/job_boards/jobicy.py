"""Read-only public Jobicy remote-jobs adapter.

The public endpoint requires no account, API key, or payment. It returns the
Jobicy listing URL; commercial direct-ATS links are intentionally not used.
"""

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://jobicy.com/api/v2/remote-jobs"
# Jobicy accepts its location slugs, while runtime profiles use ISO codes.
COUNTRY_SLUGS = dict(pair.split(':') for pair in (
    'AR:argentina AU:australia AT:austria BE:belgium BR:brazil BG:bulgaria '
    'CA:canada CN:china CR:costa-rica HR:croatia CY:cyprus CZ:czechia DK:denmark '
    'EE:estonia FI:finland FR:france DE:germany GR:greece HK:hong-kong HU:hungary '
    'IE:ireland IL:israel IT:italy JP:japan LV:latvia LT:lithuania MY:malaysia '
    'MX:mexico NL:netherlands NZ:new-zealand NO:norway PH:philippines PL:poland '
    'PT:portugal RO:romania RS:serbia SG:singapore SK:slovakia SI:slovenia '
    'KR:south-korea ES:spain SE:sweden CH:switzerland TH:thailand TR:turkiye '
    'AE:united-arab-emirates GB:uk UK:uk US:usa UA:ukraine VN:vietnam'
).split())


def _plain_text(value):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


class JobicyJobBoard:
    def __init__(self, opener=None):
        self.opener = opener or urllib.request.urlopen

    def search(self, query="", count=100, geo="", industry="", cursor=None):
        if count < 1 or count > 200:
            raise ValueError("count must be between 1 and 200")
        if query and not 3 <= len(query) <= 50:
            raise ValueError('Jobicy keyword must be between 3 and 50 characters')
        params = {"count": count}
        if query:
            params["tag"] = query
        if geo:
            params["geo"] = COUNTRY_SLUGS.get(geo.upper(), geo.lower())
        if industry:
            params["industry"] = industry
        if cursor:
            params["cursor"] = cursor
        request = urllib.request.Request(
            BASE_URL + "?" + urllib.parse.urlencode(params),
            headers={"Accept": "application/json"},
        )
        try:
            with self.opener(request) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read(1000).decode('utf-8', 'replace')
            raise RuntimeError("Jobicy returned HTTP {}: {}".format(error.code, detail))
        except urllib.error.URLError as error:
            raise RuntimeError("Jobicy unavailable: {}".format(error.reason))
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs", []), list):
            raise RuntimeError("Jobicy returned an invalid response")
        return [self._normalize(item) for item in payload["jobs"]]

    @staticmethod
    def _normalize(job):
        industries = job.get("jobIndustry") or []
        types = job.get("jobType") or []
        return {
            "company": job.get("companyName", ""),
            "role": job.get("jobTitle", ""),
            "posting_url": job.get("url", ""),
            "location": job.get("jobGeo", ""),
            "description": _plain_text(job.get("jobDescription") or job.get("jobExcerpt")),
            "category": ", ".join(industries) if isinstance(industries, list) else str(industries),
            "employment_types": types,
            "source": "jobicy",
            "source_id": str(job.get("id", "")),
            "updated_at": job.get("pubDate"),
            "salary_min": job.get("salaryMin"),
            "salary_max": job.get("salaryMax"),
            "salary_currency": job.get("salaryCurrency"),
        }
