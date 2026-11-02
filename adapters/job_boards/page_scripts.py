"""Known job-board page scripts, rendered without executing JavaScript.

A board page is a shell plus one assignment the board's script would turn into
the posting and the application form. Each PageScript reads that assignment.
Nothing here evaluates script source.
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ApplicationForm:
    action: str
    fields: tuple


@dataclass(frozen=True)
class RenderedPosting:
    company: str
    role: str
    description: str
    form: ApplicationForm
    html: str


def json_assignment(source, marker):
    """Read one `marker { ... }` object. The marker is literal text, not code."""
    start = source.find(marker)
    if start < 0:
        raise ValueError("page script is missing {}".format(marker.strip()))
    payload, _ = json.JSONDecoder().raw_decode(source[start + len(marker):].lstrip())
    if not isinstance(payload, dict):
        raise ValueError("page script payload must be an object")
    return payload


def _form(payload):
    apply = payload.get("apply") or {}
    action = apply.get("action") or ""
    fields = tuple(apply.get("fields") or ())
    if not action.startswith("https://") or not fields:
        raise ValueError("page script did not publish an application form")
    return ApplicationForm(action, fields)


def _document(company, role, description, form):
    inputs = "\n".join(
        '<input name="{}">'.format(html.escape(field, quote=True)) for field in form.fields)
    return (
        "<article>"
        "<h1>{role}</h1><p>{company}</p>"
        "<section id=\"job-description\">{description}</section>"
        "<form method=\"post\" action=\"{action}\">{inputs}"
        "<button type=\"submit\">Submit application</button></form>"
        "</article>"
    ).format(
        role=html.escape(role),
        company=html.escape(company),
        description=html.escape(description),
        action=html.escape(form.action, quote=True),
        inputs=inputs,
    )


def _rendered(company, role, description, payload):
    form = _form(payload)
    return RenderedPosting(company, role, description, form, _document(company, role, description, form))


class ComeetPageScript:
    """Comeet serializes POSITION_DATA and the apply form beside an empty widget."""

    marker = "POSITION_DATA = "

    def matches(self, source):
        return self.marker in source

    def render(self, url, source):
        from .comeet import _extract_comeet
        payload = json_assignment(source, self.marker)
        description = _extract_comeet(source)
        company = str(payload.get("company_name") or "")
        role = str(payload.get("name") or "")
        return _rendered(company, role, description, payload)


class GreenhousePageScript:
    marker = "window.__GREENHOUSE_JOB__ = "

    def matches(self, source):
        return self.marker in source

    def render(self, url, source):
        from .parsing import text
        payload = json_assignment(source, self.marker)
        return _rendered(
            str(payload.get("company") or ""),
            str(payload.get("title") or ""),
            text(str(payload.get("content") or "")),
            payload,
        )


class LeverPageScript:
    marker = "window.__LEVER_POSTING__ = "

    def matches(self, source):
        return self.marker in source

    def render(self, url, source):
        payload = json_assignment(source, self.marker)
        description = "\n".join(filter(None, [
            str(payload.get("descriptionPlain") or ""),
            str(payload.get("additionalPlain") or ""),
        ]))
        return _rendered(str(payload.get("company") or ""), str(payload.get("text") or ""), description, payload)


class AshbyPageScript:
    marker = "window.__ASHBY_JOB__ = "

    def matches(self, source):
        return self.marker in source

    def render(self, url, source):
        from .parsing import text
        payload = json_assignment(source, self.marker)
        return _rendered(
            str(payload.get("organization") or ""),
            str(payload.get("title") or ""),
            text(str(payload.get("descriptionHtml") or "")),
            payload,
        )


class PageRenderer:
    """First matching board script renders the page. Unknown pages are refused."""

    def __init__(self, scripts=None):
        self.scripts = tuple(scripts or (
            ComeetPageScript(), GreenhousePageScript(), LeverPageScript(), AshbyPageScript()))

    def render(self, url, source):
        for script in self.scripts:
            if script.matches(source):
                return script.render(url, source)
        raise ValueError("no known job-board script in the posting page")
