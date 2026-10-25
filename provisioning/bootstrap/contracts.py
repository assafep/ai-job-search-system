"""Versioned, provider-independent provisioning handoff contract.

All handoffs are secret-bearing documents. Only `vendor` may contain
provider-specific keys. `admin` means database administration, not cloud admin.
"""
from typing import Callable, Optional, Protocol
from dataclasses import dataclass
from pathlib import Path
import urllib.parse


class ProvisioningError(Exception):
    pass


class SetupError(ProvisioningError):
    pass


class DatabaseError(ProvisioningError):
    pass


def connection(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('postgres', 'postgresql') or not all(
            (parsed.hostname, parsed.username, parsed.password, parsed.path.lstrip('/'))):
        raise DatabaseError('A database connection must include host, database, role, and password.')
    return {'url': url, 'role': urllib.parse.unquote(parsed.username),
            'password': urllib.parse.unquote(parsed.password)}


def database_handoff(*, provider, name, schema, app_url, admin_url, vendor):
    result = {'schema_version': 1, 'provider': provider,
              'common': {'engine': 'postgresql', 'database': name, 'schema': schema,
                         'connections': {'app': connection(app_url), 'admin': connection(admin_url)}},
              'vendor': vendor}
    validate_database_handoff(result)
    return result


def validate_database_handoff(result):
    """Fail before executing DDL or exporting an incomplete adapter result."""
    try:
        if set(result) != {'schema_version', 'provider', 'common', 'vendor'}:
            raise ValueError
        if result['schema_version'] != 1 or not isinstance(result['provider'], str) or not result['provider']:
            raise ValueError
        common = result['common']
        if set(common) != {'engine', 'database', 'schema', 'connections'}:
            raise ValueError
        if common['engine'] != 'postgresql' or common['schema'] != 'public':
            raise ValueError
        if not isinstance(common['database'], str) or not common['database']:
            raise ValueError
        connections = common['connections']
        if set(connections) != {'app', 'admin'}:
            raise ValueError
        for value in connections.values():
            if value != connection(value['url']):
                raise ValueError
            if urllib.parse.unquote(urllib.parse.urlsplit(value['url']).path.lstrip('/')) != common['database']:
                raise ValueError
        if connections['app']['role'] == connections['admin']['role']:
            raise ValueError
        if not isinstance(result['vendor'], dict):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise DatabaseError('Invalid database provider handoff; expected contract version 1.') from None


class DatabaseProvider(Protocol):
    ENGINE: str

    def provision(self, state: dict, root: Path, command: Callable,
                  checkpoint: Callable) -> dict:
        """Ensure resources/roles, publish app URL, return a version-1 handoff.

        Must reuse remote resources and credentials on retries, detach temporary
        owner bindings, and keep admin credentials out of runtime environments.
        Table creation and final credential-file writing belong to the caller.
        """
        ...

    def verify(self, handoff: dict) -> None:
        """Verify runtime permissions after the caller creates tables."""
        ...


@dataclass
class HostingResult:
    handoff: dict
    resume_state: dict
    command: Callable
    checkpoint: Callable


class HostingProvider(Protocol):
    def provision_hosting(self, args) -> Optional[HostingResult]:
        """Return common base_url/bearer_token and provider metadata in vendor.

        `--credentials` prints provider credentials and returns None instead.
        Resume state and command are adapter context, never a consumer contract.
        """
        ...


def validate_hosting_handoff(result):
    try:
        if set(result) != {'schema_version', 'provider', 'common', 'vendor'}:
            raise ValueError
        if result['schema_version'] != 1 or not isinstance(result['provider'], str) or not result['provider']:
            raise ValueError
        if set(result['common']) != {'base_url', 'bearer_token'}:
            raise ValueError
        url = urllib.parse.urlsplit(result['common']['base_url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError
        if not isinstance(result['common']['bearer_token'], str) or not result['common']['bearer_token']:
            raise ValueError
        if not isinstance(result['vendor'], dict):
            raise ValueError
    except (TypeError, KeyError, ValueError, AttributeError):
        raise DatabaseError('Invalid hosting provider handoff; expected contract version 1.') from None
