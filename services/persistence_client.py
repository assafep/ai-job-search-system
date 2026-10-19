"""Small provider-neutral client for the persistence API.

Workflow runtimes use this client instead of importing a Drive adapter. Agents
never receive this client; they only receive serialized context.
"""

import json
import os
import base64
import re
from copy import deepcopy
from functools import partial
from time import monotonic
import urllib.error
import urllib.parse
import urllib.request
from threading import RLock
from .synchronization import synchronized


class PersistenceApiError(RuntimeError):
    pass


class PersistenceApiClient:
    def __init__(self, base_url=None, opener=None, auth_token=None, timeout=None):
        self._lock = RLock()
        self._request_count = 0
        self._active_requests = 0
        self._peak_requests = 0
        self._timings = {}
        base_url = base_url or os.environ.get("PERSISTENCE_API_URL", "http://127.0.0.1:8080")
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout if timeout is not None else os.environ.get("PERSISTENCE_TIMEOUT_SECONDS", "45"))
        if self.timeout <= 0:
            raise ValueError("persistence timeout must be positive")
        self.opener = opener or partial(urllib.request.urlopen, timeout=self.timeout)
        self.auth_token = auth_token or os.environ.get("PERSISTENCE_API_TOKEN")

    def _request(self, method, path, payload=None):
        queued_at = monotonic()
        # Keep the required single storage lane, but measure its queue separately
        # from network time. Group routes without exposing IDs or query values.
        with self._lock:
            started_at = monotonic()
            route = urllib.parse.urlsplit(path).path
            route = re.sub(r"^/applications/[^/]+", "/applications/{id}", route)
            route = re.sub(r"(/artifacts)/[^/]+$", r"\1/{id}", route)
            timing = self._timings.setdefault(method + " " + route, {
                'requests': 0, 'errors': 0, 'wait_seconds': 0.0,
                'request_seconds': 0.0, 'max_request_seconds': 0.0,
            })
            timing['requests'] += 1
            timing['wait_seconds'] += started_at - queued_at
            self._request_count += 1
            self._active_requests += 1
            self._peak_requests = max(self._peak_requests, self._active_requests)
            try:
                return self._send_request(method, path, payload)
            except Exception:
                timing['errors'] += 1
                raise
            finally:
                elapsed = monotonic() - started_at
                timing['request_seconds'] += elapsed
                timing['max_request_seconds'] = max(timing['max_request_seconds'], elapsed)
                self._active_requests -= 1

    @synchronized
    def storage_statistics(self):
        return {'requests': self._request_count, 'peak_concurrent_requests': self._peak_requests,
                'active_requests': self._active_requests, 'operations': deepcopy(self._timings)}

    def _send_request(self, method, path, payload=None):
        url = self.base_url + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, method=method, data=data)
        request.add_header("Accept", "application/json")
        if self.auth_token:
            request.add_header("Authorization", "Bearer " + self.auth_token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self.opener(request) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise PersistenceApiError("persistence API returned {}: {}".format(error.code, detail))
        except urllib.error.URLError as error:
            raise PersistenceApiError("persistence API unavailable: {}".format(error.reason))
        except TimeoutError as error:
            raise PersistenceApiError("persistence API request timed out") from error

    def list_applications(self, status=None):
        query = "" if status is None else "?" + urllib.parse.urlencode({"status": status})
        return self._request("GET", "/applications" + query)

    def journal_status(self):
        return self._request("GET", "/journal/status")

    def queue_counts(self):
        return self._request("GET", "/journal/queues")

    def repair_journal(self):
        return self._request("POST", "/journal/repair", {})

    def migrate_application_states(self):
        return self._request('POST', '/journal/migrate-application-states', {})

    def rollback_ready_applications(self):
        return self._request("POST", "/journal/rollback-ready", {})

    def delete_application(self, application_id):
        return self._request("POST", "/applications/{}/delete".format(application_id), {})

    def delete_last_event(self, application_id):
        return self._request("POST", "/applications/{}/delete-last-event".format(application_id), {})

    def get_application(self, application_id):
        return self._request("GET", "/applications/{}".format(application_id))

    def create_application(self, company, role, posting_url, target_state="Bookmarked", data=None, idempotency_key=None):
        payload = {"company": company, "role": role, "posting_url": posting_url}
        if target_state != "Bookmarked":
            payload["target_state"] = target_state
        if data is not None:
            payload["data"] = data
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        return self._request("POST", "/applications", payload)

    def transition(self, application_id, target, data=None, idempotency_key=None):
        payload = {"target": target}
        if data is not None:
            payload["data"] = data
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        return self._request("POST", "/applications/{}/transition".format(application_id), payload)

    def list_events(self, application_id):
        return self._request("GET", "/applications/{}/events".format(application_id))

    def append_event(self, application_id, event_type, data=None, idempotency_key=None):
        payload = {"event_type": event_type}
        if data is not None:
            payload["data"] = data
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
        return self._request("POST", "/applications/{}/events".format(application_id), payload)

    def store_artifact(self, application_id, name, content, mime_type=None, idempotency_key=None):
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        return self._request(
            "POST",
            "/applications/{}/artifacts".format(application_id),
            {
                "name": name,
                "mime_type": mime_type,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "idempotency_key": idempotency_key,
            },
        )

    def write_research(self, application_id, title, content, idempotency_key=None):
        return self._request(
            "POST",
            "/applications/{}/research".format(application_id),
            {"title": title, "content": content, "idempotency_key": idempotency_key},
        )

    def list_research(self, application_id):
        return self._request("GET", "/applications/{}/research".format(application_id))

    def list_artifacts(self, application_id):
        return self._request("GET", "/applications/{}/artifacts".format(application_id))

    def read_artifact(self, application_id, artifact_id):
        return self._request(
            "GET",
            "/applications/{}/artifacts/{}".format(application_id, artifact_id),
        )

    def get_profile(self):
        return self._request("GET", "/profile")

    def migrate_internal_storage(self):
        return self._request('POST', '/storage/migrate-internal', {})

    def save_profile(self, profile):
        return self._request("PUT", "/profile", profile)

    def store_file(self, path, content, mime_type=None, idempotency_key=None):
        if not isinstance(content, bytes):
            raise TypeError("file content must be bytes")
        return self._request("POST", "/files", {
            "path": path,
            "mime_type": mime_type,
            "content_base64": base64.b64encode(content).decode("ascii"),
            "idempotency_key": idempotency_key,
        })

    def read_file(self, path):
        query = urllib.parse.urlencode({"path": path})
        result = self._request("GET", "/files?" + query)
        result["content"] = base64.b64decode(result.pop("content_base64"), validate=True)
        return result
