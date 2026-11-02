"""Read a rendered posting, then submit its form once to the mocked endpoint.

The reader and the submitter share a site and a page renderer. The site is the
only I/O. A missing confirmation is not a successful submission.
"""
from __future__ import annotations

import hashlib

THANK_YOU = "Thank you for applying"


class PostingReader:
    """GET the posting URL and return the fields bookmark_discovered expects."""

    def __init__(self, site, renderer):
        self.site = site
        self.renderer = renderer

    def __call__(self, url):
        page = self.renderer.render(url, self.site.get_text(url))
        return {
            "company": page.company,
            "role": page.role,
            "description": page.description,
            "resolved_url": url,
            "form_action": page.form.action,
        }


class PreparedFormSubmission:
    """POST the prepared package to the form published on the posting page."""

    def __init__(self, site, renderer, store):
        self.site = site
        self.renderer = renderer
        self.store = store

    def __call__(self, item):
        prepared = next(
            (event.get("outcome", {}).get("data", {}) for event in reversed(item.get("events") or [])
             if (event.get("outcome") or {}).get("code") == "PREPARED"),
            None)
        if not prepared or not prepared.get("path"):
            return {"status": "Blocked", "blockers": ["Verified prepared package reference is missing"]}
        package = self.store.read_path(prepared["path"])["content"]
        if not isinstance(package, bytes) or not package.startswith(b"%PDF-"):
            return {"status": "Blocked", "blockers": ["Prepared artifact is not a PDF"]}
        page = self.renderer.render(item["posting_url"], self.site.get_text(item["posting_url"]))
        status, body, _ = self.site.post_form(page.form.action, {
            "posting_url": item["posting_url"],
            "role": item.get("role") or page.role,
            "package_name": "submission-package.pdf",
            "package_sha256": hashlib.sha256(package).hexdigest(),
        })
        text = body.decode("utf-8", "replace")
        if status == 200 and THANK_YOU in text:
            return {"status": "Submitted successfully", "confirmation_text": THANK_YOU}
        return {
            "status": "Submission unconfirmed",
            "blockers": ["Application endpoint returned no thank-you confirmation"],
        }
