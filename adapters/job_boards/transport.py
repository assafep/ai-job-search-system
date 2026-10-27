"""Shared bounded HTTP transport and synchronized proxy health, independent of boards."""

import csv
import ipaddress
import random
import threading
import json
from io import BytesIO
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit
import urllib.request
import urllib.error

from .configuration import ROOT

DEFAULT_PROXIES = ROOT / 'config' / 'free_proxies.csv'

class ProxyPool:
    """Thread-safe process-local health; never modify the operator's CSV."""

    def __init__(self, path=DEFAULT_PROXIES):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.signature = None
        self.health = {}
        self.proxies = []

    def choose(self, excluded):
        with self.lock:
            try:
                stat = self.path.stat()
            except FileNotFoundError:
                return None
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature != self.signature:
                proxies = []
                with self.path.open(newline='', encoding='utf-8-sig') as source:
                    rows = csv.DictReader(source)
                    if not {'ip', 'port', 'globally_routable'} <= set(rows.fieldnames or []):
                        raise ValueError('proxy CSV requires ip,port,globally_routable')
                    for row in rows:
                        try:
                            address = ipaddress.ip_address(row['ip'].strip())
                            port = int(row['port'])
                        except (ValueError, AttributeError, TypeError):
                            continue
                        if row['globally_routable'].strip().lower() != 'true' or not address.is_global or not 1 <= port <= 65535:
                            continue
                        host = '[{}]'.format(address) if address.version == 6 else str(address)
                        proxies.append('http://{}:{}'.format(host, port))
                self.proxies = list(dict.fromkeys(proxies))
                self.health = {p: self.health.get(p, (0, 0, 0)) for p in self.proxies}
                self.signature = signature
            now = monotonic()
            candidates = [p for p in self.proxies if p not in excluded and self.health[p][1] <= now]
            if not candidates:
                return None
            # Randomized ties prevent every process starting at CSV row one.
            random.shuffle(candidates)
            proxy = min(candidates, key=lambda p: self.health[p][2])
            failures, _, _ = self.health[proxy]
            self.health[proxy] = (failures, now + 30, now)
            return proxy

    def record(self, proxy, success):
        with self.lock:
            failures, _, used = self.health.get(proxy, (0, 0, 0))
            failures = 0 if success else failures + 1
            self.health[proxy] = (failures, 0 if success else monotonic() + min(900, 30 * 2 ** min(failures, 5)),
                                  -1 if success else used)


_pool = ProxyPool()
_pools = {}
_pools_lock = threading.Lock()


def proxy_pool(path):
    path = Path(path).resolve()
    with _pools_lock:
        if path == _pool.path.resolve():
            return _pool
        return _pools.setdefault(path, ProxyPool(path))


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('public posting URL must use HTTP or HTTPS without credentials')
    return parsed


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, limit):
        self.max_redirections = limit
        self.max_repeats = limit

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        if req.full_url.startswith('https:') and not newurl.startswith('https:'):
            raise ValueError('posting redirected away from HTTPS')
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        # Custom API headers (including credentials) must not cross origins.
        if result is not None and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            result.headers = {key: value for key, value in result.headers.items()
                              if key.lower() in {'accept', 'user-agent', 'accept-language', 'cache-control', 'pragma'}}
        return result


class ReadSession:
    """One operation/route. Every request consumes the same absolute deadline."""

    def __init__(self, policy, *, deadline=None, proxy=None, source_reader=None,
                 opener=None, description_reader=None, clock=monotonic):
        self.policy = policy
        self.clock = clock
        self.deadline = deadline if deadline is not None else clock() + policy.timeout_seconds
        self.proxy = proxy
        self.source_reader = source_reader
        self.opener = opener
        self.description_reader = description_reader
        self.resolved = {}

    def read_text(self, url, headers=None):
        parsed = validate_url(url)
        if self.proxy and parsed.scheme != 'https':
            raise ValueError('proxy routes require HTTPS')
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError('retrieval time budget exhausted')
        timeout = min(self.policy.timeout_seconds, remaining)
        headers = {'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache',
                   'Pragma': 'no-cache', **(headers or {})}
        if self.source_reader is not None:
            source = self.source_reader(url, headers, timeout)
            body, final_url = source.encode('utf-8'), url
        elif self.policy.backend == 'scrapling' and self.opener is None:
            from scrapling.fetchers import Fetcher
            try:
                page = Fetcher.get(url, proxy=self.proxy, headers=headers,
                                   impersonate=self.policy.impersonate, timeout=timeout,
                                   retries=1, follow_redirects='safe',
                                   max_redirects=self.policy.max_redirects, verify=True)
            except Exception as exc:
                # Transport messages can include proxy credentials and page data.
                raise OSError('Scrapling transport failed: ' + type(exc).__name__) from None
            if page.status != 200:
                raise OSError('HTTP {}'.format(page.status))
            body, final_url = page.body, str(page.url)
        else:
            request = urllib.request.Request(url, headers=headers)
            opener = self.opener or urllib.request.build_opener(
                urllib.request.ProxyHandler({'https': self.proxy} if self.proxy else {}),
                _Redirects(self.policy.max_redirects)).open
            try:
                with opener(request, timeout=timeout) as response:
                    body = response.read(self.policy.max_response_bytes + 1)
                    final_url = response.geturl() if hasattr(response, 'geturl') else url
            except urllib.error.HTTPError as exc:
                raise OSError('HTTP {}'.format(exc.code)) from None
            except urllib.error.URLError:
                raise OSError('HTTP transport unavailable') from None
        validate_url(final_url)
        if parsed.scheme == 'https' and urlsplit(final_url).scheme != 'https':
            raise ValueError('posting redirected away from HTTPS')
        if len(body) > self.policy.max_response_bytes:
            raise ValueError('posting response exceeds configured size limit')
        if self.clock() >= self.deadline:
            raise TimeoutError('retrieval time budget exhausted')
        self.resolved[url] = final_url
        return body.decode('utf-8', 'replace')

    def read_json(self, url, headers=None):
        return json.loads(self.read_text(url, {'Accept': 'application/json', **(headers or {})}))

    def open(self, request):
        """Small urllib-compatible facade for existing discovery parsers."""
        return BytesIO(self.read_text(request.full_url, dict(request.header_items())).encode('utf-8'))

    def details(self, posting_url, source_url, description):
        return {'posting_url': posting_url, 'source_url': self.resolved.get(source_url, source_url),
                'description': description}
