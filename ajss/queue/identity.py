"""Deterministic posting identity; historical PostgreSQL key format is preserved."""
import re
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode

def normalize(value):
    return re.sub(r"\s+", " ", value or "").strip().casefold()

TRACKING_KEYS=frozenset(("utm_source","utm_medium","utm_campaign","utm_term","utm_content","fbclid","gclid"))

def canonical_legacy_url(value):
    parsed=urlsplit((value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return (value or "").strip()
    query=urlencode([(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True)
                     if k.lower() not in TRACKING_KEYS])
    return urlunsplit((parsed.scheme.lower(),parsed.netloc.lower(),parsed.path.rstrip("/") or "/",query,""))

def historical_keys(company,role,url="",source_id=""):
    """The old posting_keys index used 'company / role' and normalized URLs.

    Preserve that format so the historical entries are matched without scanning
    the Drive application directory or reapplying to already completed jobs.
    """
    keys=[]
    if source_id:keys.append(("source",normalize(source_id)))
    if url:keys.append(("url",canonical_legacy_url(url)))
    if company and role:keys.append(("company_role",normalize(company)+" / "+normalize(role)))
    return keys

