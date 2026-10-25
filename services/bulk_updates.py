"""Build stable item keys and propagate partial failures to workflow bookkeeping."""
import hashlib
import json


def item(operation, *, application=None, data=None, version=None):
    payload = {'operation': operation}
    if application is not None:
        def value(key):
            return application.get(key) if isinstance(application, dict) else getattr(application, key, None)
        payload['application_id'] = value('application_id')
        payload['expected_status'] = value('status')
        if value('updated_at'):
            payload['expected_updated_at'] = value('updated_at')
    if data is not None:
        payload['data'] = data
    material = json.dumps([payload, version], sort_keys=True, separators=(',', ':')).encode()
    payload['idempotency_key'] = 'bulk-' + operation + '-' + hashlib.sha256(material).hexdigest()
    return payload


def require_success(response):
    if response.get('failed') or any(r.get('status') != 'succeeded' for r in response.get('results', [])):
        from .application_service_client import BulkUpdateError
        raise BulkUpdateError(response)
    return response['results']
