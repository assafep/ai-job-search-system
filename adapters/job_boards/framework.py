"""Service entry point for job boards: registry, API-first reads and host submission.

Adapters never import services. Read retries share a deadline; submission never
uses the retry engine. Instances have no per-operation mutable state and can be
shared by concurrent workers; proxy health is synchronized by the transport.
"""

from dataclasses import replace
from time import monotonic
from urllib.parse import urlsplit

from .configuration import BoardConfiguration
from .default import DefaultJobBoardAdapter
from .interface import AdapterResult, BookmarkContext, JobBoardAdapter, MissingCredentials, UnsupportedOperation
from .submission import validate_submission_outcome
from .transport import ReadSession, proxy_pool, validate_url


def builtin_adapters():
    from .adzuna import AdzunaJobBoard
    from .ashby import AshbyJobBoard
    from .comeet import ComeetJobBoard
    from .freehire import FreehireJobBoard
    from .greenhouse import GreenhouseJobBoard
    from .hibob import HiBobJobBoard
    from .himalayas import HimalayasJobBoard
    from .jobicy import JobicyJobBoard
    from .lever import LeverJobBoard
    from .linkedin import LinkedInJobBoard
    from .nimble import NimbleJobBoard
    from .oracle import OracleJobBoard
    from .smartrecruiters import SmartRecruitersJobBoard
    return [cls() for cls in (AdzunaJobBoard, AshbyJobBoard, ComeetJobBoard,
            FreehireJobBoard, GreenhouseJobBoard, HiBobJobBoard, HimalayasJobBoard,
            JobicyJobBoard, LeverJobBoard, LinkedInJobBoard, NimbleJobBoard,
            OracleJobBoard, SmartRecruitersJobBoard)]


class AdapterFramework:
    def __init__(self, opener=None, timeout=None, proxy_pool=None, *, config=None,
                 config_path=None, adapters=None, source_reader=None, deadline=None,
                 description_reader=None, submitter=None, clock=None):
        self.config = config if isinstance(config, BoardConfiguration) else BoardConfiguration(config_path, config)
        self.opener = opener
        self.timeout = timeout
        self.pool = proxy_pool
        self.source_reader = source_reader
        self.deadline = deadline
        self.description_reader = description_reader
        self.submitter = submitter
        self.clock = clock or monotonic
        self._default_adapter = DefaultJobBoardAdapter()
        self._adapters = {}
        for adapter in builtin_adapters() if adapters is None else adapters:
            self.register_adapter(adapter)

    def register_adapter(self, adapter):
        if not isinstance(adapter, JobBoardAdapter):
            raise TypeError('Adapter must implement JobBoardAdapter')
        self._adapters[adapter.provider_name] = adapter

    def get_adapter(self, provider_name):
        if provider_name == 'default':
            return self._default_adapter
        try:
            return self._adapters[provider_name]
        except KeyError:
            raise ValueError('Unknown job board provider: ' + provider_name) from None

    def resolve_provider(self, posting_url):
        host = (validate_url(posting_url).hostname or '').lower()
        return next((name for name, adapter in self._adapters.items() if adapter.matches_host(host)), 'default')

    def search(self, context, provider_name):
        if type(context.limit) is not int or context.limit < 1:
            raise ValueError('Search limit must be a positive integer')
        if type(context.offset) is not int or context.offset < 0:
            raise ValueError('Search offset must be a nonnegative integer')
        if type(context.page) is not int or context.page < 1:
            raise ValueError('Search page must be a positive integer')
        return self._read('search', context, provider_name)

    def list_jobs(self, context, provider_name):
        return self._read('list_jobs', context, provider_name)

    def discover(self, context, provider_name):
        """Explicit employer scope lists jobs; otherwise run a search."""
        return self.list_jobs(context, provider_name) if context.boards else self.search(context, provider_name)

    def fetch_details(self, context, provider_name=None):
        validate_url(context.posting_url)
        return self._read('fetch_details', context, provider_name or self.resolve_provider(context.posting_url))

    def _read(self, operation, context, provider):
        adapter = self.get_adapter(provider)
        budget, policies = self.config.for_provider(provider)
        deadline = min(self.deadline, self.clock() + budget) if self.deadline is not None else self.clock() + budget
        # No state is assigned to the adapter while an operation is in flight.
        attempts = []
        for mode, suffix in (('api', 'api'), ('scraping', 'scrape')):
            policy = policies[mode]
            if self.timeout is not None:
                policy = replace(policy, timeout_seconds=self.timeout)
            pool = self.pool or proxy_pool(policy.proxy_file)
            excluded = set()
            for index in range(int(policy.direct) + policy.proxy_attempts):
                if self.clock() >= deadline:
                    attempts.append({'mode': mode, 'error': 'retrieval time budget exhausted'})
                    break
                proxy = None
                if not (policy.direct and index == 0):
                    if isinstance(context, BookmarkContext) and urlsplit(context.posting_url).scheme != 'https':
                        break
                    proxy = pool.choose(excluded)
                    if proxy is None:
                        attempts.append({'mode': mode, 'route': 'proxy',
                                         'error': 'no healthy proxy route available'})
                        break
                    excluded.add(proxy)
                session = ReadSession(policy, deadline=deadline, proxy=proxy,
                                      source_reader=self.source_reader, opener=self.opener,
                                      description_reader=self.description_reader, clock=self.clock)
                route = 'proxy' if proxy else 'direct'
                try:
                    data = getattr(adapter, operation + '_' + suffix)(context, session)
                    self._validate(operation, context, data)
                except (UnsupportedOperation, MissingCredentials) as exc:
                    attempts.append({'mode': mode, 'route': route, 'error': str(exc)})
                    # Missing capabilities/credentials do not change via a proxy.
                    break
                except (OSError, ValueError, RuntimeError) as exc:
                    if proxy:
                        pool.record(proxy, False)
                    attempts.append({'mode': mode, 'route': route, 'error': str(exc)})
                else:
                    if proxy:
                        pool.record(proxy, True)
                    return AdapterResult(True, data=data, metadata={
                        'provider': provider, 'mode': mode, 'route': route, 'attempts': attempts})
        prefix = 'full job posting unavailable' if operation == 'fetch_details' else 'job discovery unavailable'
        return AdapterResult(False, error=prefix + ' within bounded routes: ' + '; '.join(
            '{}: {}'.format(item['mode'], item['error']) for item in attempts),
            metadata={'provider': provider, 'attempts': attempts})

    @staticmethod
    def _validate(operation, context, data):
        if operation == 'fetch_details':
            if not isinstance(data, dict) or data.get('posting_url') != context.posting_url:
                raise ValueError('Adapter returned a different posting URL')
            if not isinstance(data.get('description'), str) or len(data['description']) < 500:
                raise ValueError('no full job description in response')
            validate_url(data.get('source_url', ''))
        elif not isinstance(data, list) or any(
                not isinstance(item, dict) or not item.get('posting_url') or not item.get('role') for item in data):
            raise ValueError('Adapter returned invalid discovery records')
        else:
            for item in data:
                validate_url(item['posting_url'])

    def submit(self, context, provider_name=None):
        """Dispatch once. Exceptions propagate into the durable receipt boundary."""
        adapter = self.get_adapter(provider_name or self.resolve_provider(context.posting_url))
        try:
            data = adapter.submit_application(context, self.submitter)
        except UnsupportedOperation as exc:
            data = {'status': 'Blocked', 'failure_kind': 'system_error', 'blockers': [str(exc)]}
            validate_submission_outcome(data)
            return AdapterResult(False, error=str(exc), data=data,
                                 metadata={'provider': adapter.provider_name})
        validate_submission_outcome(data)
        return AdapterResult(True, data=data, metadata={'provider': adapter.provider_name})


def get_framework(**kwargs):
    """Build an isolated configuration snapshot; callers may retain it for a run."""
    return AdapterFramework(**kwargs)


def fetch_posting_details(posting_url, **kwargs):
    return get_framework(**kwargs).fetch_details(BookmarkContext(posting_url)).unwrap()
