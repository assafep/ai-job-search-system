"""Bounded, typed bulk mutations with durable per-item retry receipts.

A batch is independent ordered operations, not a distributed transaction. An
intent is saved before each mutation. A lost result is never blindly replayed.
"""
from contextlib import nullcontext
from dataclasses import asdict, is_dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from threading import RLock
import urllib.error

import yaml
from jsonschema import Draft202012Validator

from .storage_layout import workload_state_path

LOCK = RLock()


class Conflict(ValueError):
    pass


@lru_cache(maxsize=1)
def validator():
    from .mcp_adapter import _resolve
    document = yaml.safe_load((Path(__file__).resolve().parents[1] / 'openapi.yaml').read_text())
    return Draft202012Validator(_resolve(document['components']['schemas']['BulkUpdateRequest'], document))


def plain(value):
    return asdict(value) if is_dataclass(value) else value


def receipt_path(key):
    return workload_state_path('bulk-updates', hashlib.sha256(key.encode()).hexdigest() + '.json')


def load_receipt(repository, path):
    try:
        stored = repository.read_file(path)
    except KeyError:
        return None
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return None
    return json.loads(stored['content'])


def save_receipt(repository, path, receipt):
    repository.store_file(path, json.dumps(receipt).encode(), 'application/json')


def apply(repository, item):
    operation = item['operation']
    data = item.get('data') or {}
    identity = item.get('application_id')
    if identity:
        application = plain(repository.get_application(identity))
        if application is None:
            raise KeyError(identity)
        for field in ('status', 'updated_at'):
            expected = item.get('expected_' + field)
            if expected is not None and application.get(field) != expected:
                raise Conflict('Application changed since it was selected')
    if operation == 'update_application':
        return plain(repository.update_application(identity, **data))
    if operation == 'transition_application':
        return plain(repository.transition(identity, data['target'], data.get('data'), item['idempotency_key']))
    if operation == 'append_event':
        return plain(repository.append_event(identity, data['event_type'], data.get('data'), item['idempotency_key']))
    if operation == 'complete_application':
        if 'inactive_before' in data:
            from services.ghosting_stage import last_activity, _stamp
            cutoff = _stamp(data['inactive_before'])
            if cutoff is None:
                raise ValueError('inactive_before must be an ISO timestamp')
            activity = last_activity(application, repository.list_events(identity))
            if activity is None or activity > cutoff:
                raise Conflict('Application no longer satisfies the inactivity cutoff')
        from .terminal_journal import complete_application
        return plain(complete_application(repository, identity, data['reason']))
    if operation == 'remove_duplicate':
        if application['status'] != 'Duplicate':
            raise Conflict('Only Duplicate items can be removed by this operation')
        if hasattr(repository, 'remove_tab'):
            repository.remove_tab(identity)
        else:
            repository.delete_application(identity)
        return {'application_id': identity, 'action': 'removed'}
    if operation == 'install_gmail_grant':
        from .gmail_grant import install_grant
        return install_grant(data)
    if operation == 'acknowledge_gmail_notice':
        from .gmail_grant import acknowledge_notice
        return acknowledge_notice(data['refresh_expires_at'])
    raise ValueError('Unsupported bulk operation')


def execute_item(repository, item):
    path = receipt_path(item['idempotency_key'])
    fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    old = load_receipt(repository, path)
    if old:
        if old['fingerprint'] != fingerprint:
            return {'status': 'failed', 'error': {'code': 'idempotency_conflict', 'message': 'Item key already used with another payload'}}
        if old['status'] == 'running':
            return {'status': 'reconciliation_required', 'error': {'code': 'uncertain_outcome', 'message': 'Prior mutation outcome requires manual reconciliation'}}
        return old['outcome']
    receipt = {'fingerprint': fingerprint, 'status': 'running'}
    save_receipt(repository, path, receipt)
    try:
        result = apply(repository, item)
        outcome = {'status': 'succeeded', 'result': result}
    except Conflict:
        outcome = {'status': 'failed', 'error': {'code': 'precondition_failed', 'message': 'Item changed or is no longer eligible'}}
    except KeyError:
        outcome = {'status': 'failed', 'error': {'code': 'not_found', 'message': 'Item not found'}}
    except ValueError:
        outcome = {'status': 'failed', 'error': {'code': 'invalid_update', 'message': 'Update violates domain validation'}}
    except Exception:
        # Even an upstream error can follow a successful external write.
        outcome = {'status': 'reconciliation_required', 'error': {'code': 'uncertain_outcome', 'message': 'Mutation failed or its outcome is uncertain; reconcile before retrying'}}
    receipt.update(status=outcome['status'], outcome=outcome)
    save_receipt(repository, path, receipt)
    return outcome


def execute(repository, data):
    if not validator().is_valid(data):
        raise ValueError('Invalid bulk update: supply 1–25 typed items with non-empty idempotency keys')
    keys = [item['idempotency_key'] for item in data['items']]
    if any(not key.strip() for key in keys) or len(set(keys)) != len(keys):
        raise ValueError('Bulk item keys must be non-empty and unique within a batch')
    # Check semantic payload errors before any item writes.
    from .journal_format import ALL_STATES
    for item in data['items']:
        if item['operation'] == 'transition_application' and item['data']['target'] not in ALL_STATES:
            raise ValueError('Invalid transition target')
        if item['operation'] == 'complete_application' and 'inactive_before' in item['data']:
            from services.ghosting_stage import _stamp
            if _stamp(item['data']['inactive_before']) is None:
                raise ValueError('inactive_before must be an ISO timestamp')
    results = []
    for index, item in enumerate(data['items']):
        try:
            scope = repository.mutation_scope() if hasattr(repository, 'mutation_scope') else nullcontext()
            with LOCK, scope:
                outcome = execute_item(repository, item)
        except Exception:
            outcome = {'status': 'reconciliation_required', 'error': {'code': 'receipt_unavailable', 'message': 'Retry receipt unavailable; reconcile item state before retrying'}}
        result = dict(index=index, operation=item['operation'], idempotency_key=item['idempotency_key'], **outcome)
        if item.get('application_id'):
            result['application_id'] = item['application_id']
        results.append(result)
    succeeded = sum(item['status'] == 'succeeded' for item in results)
    return {'results': results, 'succeeded': succeeded, 'failed': len(results) - succeeded}
