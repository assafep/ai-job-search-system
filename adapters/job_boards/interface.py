"""Board capabilities. The framework supplies transport; adapters parse sources.

Unsupported capabilities are explicit, not empty successful results. Only read
operations have API/scraping alternatives. Submission is a single host callback.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class UnsupportedOperation(Exception):
    """This adapter cannot perform this operation using this mechanism."""


class MissingCredentials(RuntimeError):
    """The API is unavailable; a public scraping alternative may still work."""


@dataclass
class AdapterResult:
    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def unwrap(self):
        if not self.success:
            raise ValueError(self.error)
        return self.data


@dataclass
class DiscoveryContext:
    query: str = ""
    countries: list = field(default_factory=list)
    locations: list = field(default_factory=list)
    limit: int = 100
    offset: int = 0
    page: int = 1
    max_pages: int = 3
    boards: dict = field(default_factory=dict)
    profile: dict = field(default_factory=dict)


@dataclass
class BookmarkContext:
    posting_url: str
    company: str = ""
    role: str = ""


@dataclass
class SubmissionContext:
    application: dict
    posting_url: str
    profile: dict = field(default_factory=dict)
    package_bytes: bytes = b""
    package_name: str = ""
    host_context: dict = field(default_factory=dict)


class JobBoardAdapter(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable configuration/registry key."""

    hosts: tuple[str, ...] = ()
    host_suffixes: tuple[str, ...] = ()

    def matches_host(self, host):
        return host in self.hosts or any(host.endswith('.' + suffix) for suffix in self.host_suffixes)

    def search_api(self, context, transport):
        raise UnsupportedOperation('API search is unavailable')

    def list_jobs_api(self, context, transport):
        raise UnsupportedOperation('API listing is unavailable')

    def fetch_details_api(self, context, transport):
        raise UnsupportedOperation('API details are unavailable')

    def discovery_pages(self, context):
        """Yield (public page URL, employer) for structured HTML discovery."""
        raise UnsupportedOperation('No public discovery page is configured')

    def search_scrape(self, context, transport):
        from .parsing import scrape_listings
        return scrape_listings(self, context, transport, search=True)

    def list_jobs_scrape(self, context, transport):
        from .parsing import scrape_listings
        return scrape_listings(self, context, transport)

    def fetch_details_scrape(self, context, transport):
        from .parsing import scrape_details
        return scrape_details(context.posting_url, transport)

    def submit_application(self, context, submitter):
        """Call the native host integration once; never retry a submission."""
        if submitter is None:
            raise UnsupportedOperation('Submission requires ChatGPT native Browser/Computer Use')
        return submitter(context.host_context or {
            'application': context.application, 'applicant_profile': context.profile,
            'package_bytes': context.package_bytes, 'package': {'name': context.package_name},
        })

    def _open(self, request, transport=None):
        """Compatibility for direct adapter callers; stages always inject transport."""
        if transport is not None:
            return transport.open(request)
        import urllib.request
        if self.opener is not urllib.request.urlopen:
            return self.opener(request)
        from .transport import ReadSession
        from .configuration import FetchPolicy
        return ReadSession(FetchPolicy(timeout_seconds=getattr(self, 'timeout', 30))).open(request)


class EmployerJobBoard(JobBoardAdapter):
    """Employer feeds list jobs; search is intentionally a separate capability."""

    def list_jobs_api(self, context, transport):
        boards = context.boards or self.boards
        if not boards:
            raise UnsupportedOperation('Employer board identifiers are required')
        return self.list_jobs(boards=boards, _transport=transport)
