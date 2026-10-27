"""Validated immutable transport policy with default and provider overrides."""

from dataclasses import dataclass, fields
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / 'config' / 'job-boards.json'


@dataclass(frozen=True)
class FetchPolicy:
    backend: str = 'urllib'
    timeout_seconds: float = 6
    direct: bool = True
    proxy_attempts: int = 0
    proxy_file: str = str(ROOT / 'config' / 'free_proxies.csv')
    impersonate: str = 'chrome'
    max_redirects: int = 5
    max_response_bytes: int = 3_000_000

    def __post_init__(self):
        if self.backend not in {'urllib', 'scrapling'}:
            raise ValueError('backend must be urllib or scrapling')
        _positive(self.timeout_seconds, 'timeout_seconds')
        if type(self.direct) is not bool:
            raise ValueError('direct must be a boolean')
        for name, minimum, maximum in [('proxy_attempts', 0, 20), ('max_redirects', 0, 20),
                                       ('max_response_bytes', 1, 50_000_000)]:
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f'{name} must be an integer between {minimum} and {maximum}')
        if not self.direct and not self.proxy_attempts:
            raise ValueError('at least one direct or proxy route is required')
        if not isinstance(self.proxy_file, str) or not self.proxy_file:
            raise ValueError('proxy_file must be a nonempty path')
        if not isinstance(self.impersonate, str) or not self.impersonate:
            raise ValueError('impersonate must be nonempty')


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + ' must be finite and positive')


class BoardConfiguration:
    def __init__(self, path=None, data=None):
        path = Path(path or os.environ.get('JOB_BOARDS_CONFIG', DEFAULT_CONFIG))
        if data is None:
            with path.open() as handle:
                data = json.load(handle)
        if not isinstance(data, dict) or set(data) - {'defaults', 'providers'}:
            raise ValueError('job board config requires defaults and providers objects')
        defaults, providers = data.get('defaults', {}), data.get('providers', {})
        if not isinstance(providers, dict):
            raise ValueError('providers must be an object')
        self._validate_section(defaults)
        for name, section in providers.items():
            if not isinstance(name, str) or not name:
                raise ValueError('provider names must be nonempty')
            self._validate_section(section)
        # Retain the old posting-fetcher environment controls as default overrides.
        defaults = {**defaults, 'api': dict(defaults.get('api', {})),
                    'scraping': dict(defaults.get('scraping', {}))}
        if 'POSTING_FETCH_BUDGET_SECONDS' in os.environ:
            defaults['budget_seconds'] = float(os.environ['POSTING_FETCH_BUDGET_SECONDS'])
        for key, env, convert in [('timeout_seconds', 'POSTING_REQUEST_TIMEOUT_SECONDS', float),
                                  ('proxy_attempts', 'POSTING_PROXY_ATTEMPTS', int),
                                  ('proxy_file', 'POSTING_PROXY_FILE', str)]:
            if env in os.environ:
                defaults['scraping'][key] = convert(os.environ[env])
                if key == 'timeout_seconds':
                    defaults['api'][key] = convert(os.environ[env])
        self._policies = {}
        for name in ['default', *providers]:
            override = providers.get(name, {})
            budget = override.get('budget_seconds', defaults.get('budget_seconds', 25))
            _positive(budget, 'budget_seconds')
            policies = {}
            for mode in ('api', 'scraping'):
                values = {**defaults.get(mode, {}), **override.get(mode, {})}
                if 'proxy_file' in values:
                    proxy_path = Path(values['proxy_file'])
                    values['proxy_file'] = str(proxy_path if proxy_path.is_absolute() else path.parent / proxy_path)
                policies[mode] = FetchPolicy(**values)
            self._policies[name] = budget, policies

    @staticmethod
    def _validate_section(section):
        if not isinstance(section, dict) or set(section) - {'budget_seconds', 'api', 'scraping'}:
            raise ValueError('unknown job board configuration key')
        allowed = {item.name for item in fields(FetchPolicy)}
        for mode in ('api', 'scraping'):
            values = section.get(mode, {})
            if not isinstance(values, dict) or set(values) - allowed:
                raise ValueError('unknown ' + mode + ' strategy option')

    def for_provider(self, name):
        return self._policies.get(name, self._policies['default'])
