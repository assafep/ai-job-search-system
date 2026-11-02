"""In-process stand-in for a job board. Unregistered URLs are refused.

Tests register the posting page and the form endpoint. No socket is opened.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl


@dataclass(frozen=True)
class Exchange:
    method: str
    url: str
    body: bytes
    status: int


class ScriptedSite:
    def __init__(self):
        self._routes = {}
        self.exchanges = []

    def route(self, method, url, body, status=200, content_type="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._routes[(method.upper(), url)] = (status, body, content_type)
        return self

    def exchange(self, method, url, body=b""):
        key = (method.upper(), url)
        if key not in self._routes:
            raise LookupError("no mocked endpoint for {} {}".format(method.upper(), url))
        status, payload, content_type = self._routes[key]
        self.exchanges.append(Exchange(key[0], url, body, status))
        return status, payload, content_type

    def get_text(self, url):
        status, payload, _ = self.exchange("GET", url)
        if status != 200:
            raise OSError("HTTP {}".format(status))
        return payload.decode("utf-8")

    def post_form(self, url, fields):
        body = "&".join("{}={}".format(key, value) for key, value in fields.items()).encode("utf-8")
        return self.exchange("POST", url, body)

    def posts(self):
        return [item for item in self.exchanges if item.method == "POST"]

    @staticmethod
    def form_fields(exchange):
        return dict(parse_qsl(exchange.body.decode("utf-8"), keep_blank_values=True))
