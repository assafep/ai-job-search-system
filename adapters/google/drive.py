"""Google Drive artifact storage for application folders."""

import http.client
import json
import os
import mimetypes
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from io import BytesIO
from time import monotonic

from .auth import access_token_value
from .paths import normalize_file_path


FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


# Address bar after a folder is open. Query strings such as ?usp=sharing are ignored.
FOLDER_URL_RE = re.compile(r"https://drive\.google\.com/drive/(?:u/\d+/)?folders/[^/?#]+")


def is_drive_folder_url(value):
    """True for the address-bar URL of an open Drive folder."""
    return bool(FOLDER_URL_RE.search(value or ""))


def folder_id_from_url(value):
    # Drive emits both /drive/folders/<id> and /drive/u/0/folders/<id> links.
    match = re.search(r"/drive/(?:u/\d+/)?folders/([^/?]+)", value or "")
    return match.group(1) if match else value


def safe_folder_name(value):
    value = re.sub(r"[\\:*?\"<>|]+", "_", (value or "").strip())
    return re.sub(r"\s+", " ", value).lstrip(' ').rstrip(' .') or "Unnamed"


APPLICATIONS_DIRECTORY = "Applications"


class DriveTransport:
    """One HTTPS connection per host, reused for later Drive calls.

    A server that closes an idle connection is dialed once more. HTTP error
    responses are returned to the caller and are not retried.
    """

    def __init__(self, timeout, connector=None):
        self.timeout = timeout
        self._connector = connector or (
            lambda host: http.client.HTTPSConnection(host, timeout=timeout)
        )
        self._connections = {}
        self._lock = threading.Lock()

    def exchange(self, method, url, data=None, headers=None):
        headers = dict(headers or {})
        for _redirect in range(5):
            parts = urllib.parse.urlsplit(url)
            if parts.scheme != "https" or not parts.hostname:
                raise ValueError("Drive requests must use https")
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            status, response_headers, body = self._round_trip(
                parts.hostname, method, path, data, headers,
            )
            if status in (301, 302, 303, 307, 308):
                location = response_headers.get("Location") or response_headers.get("location")
                if not location:
                    break
                target = urllib.parse.urljoin(url, location)
                moved = urllib.parse.urlsplit(target)
                if moved.hostname != parts.hostname:
                    return self._read_elsewhere(target)
                if status in (301, 302, 303):
                    method = "GET"
                    data = None
                url = target
                continue
            if status >= 400:
                raise urllib.error.HTTPError(
                    url, status, "Drive request failed", response_headers, BytesIO(body),
                )
            return body
        raise urllib.error.HTTPError(url, status, "Drive redirect failed", response_headers, BytesIO(body))

    def _round_trip(self, host, method, path, data, headers):
        with self._lock:
            for attempt in (1, 2):
                connection = self._connections.get(host)
                if connection is None:
                    connection = self._connector(host)
                    self._connections[host] = connection
                try:
                    connection.request(method, path, body=data, headers=headers)
                    response = connection.getresponse()
                    body = response.read()
                except urllib.error.HTTPError:
                    raise
                except (http.client.HTTPException, OSError, TimeoutError):
                    self._drop(host)
                    if attempt == 2:
                        raise
                    continue
                if response.headers.get("connection", "").lower() == "close":
                    self._drop(host)
                return response.status, response.headers, body
        raise RuntimeError("Drive connection was not attempted")

    def _drop(self, host):
        connection = self._connections.pop(host, None)
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _read_elsewhere(self, url):
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()


def application_folder_name(company, role):
    """Return the repository's single-level ``Company / Role`` folder name."""
    label = " - ".join(part.strip() for part in (company, role) if part and part.strip())
    # A slash is intentional here: it is part of the existing Drive folder name
    # convention and does not create another folder in Google Drive.
    return safe_folder_name(label.replace('/', '-'))


class GoogleDriveArtifactStore:
    def __init__(self, access_token, root_folder_url, application_root_path=None):
        self.access_token = access_token
        self.root_folder_id = folder_id_from_url(root_folder_url)
        self.application_root_path = normalize_file_path(application_root_path) if application_root_path else APPLICATIONS_DIRECTORY
        self.timeout = float(os.environ.get('GOOGLE_API_TIMEOUT_SECONDS', '30'))
        self.metadata_cache_seconds = float(os.environ.get('DRIVE_METADATA_CACHE_SECONDS', '60'))
        if self.timeout <= 0 or self.metadata_cache_seconds < 0:
            raise ValueError('Google API timeout must be positive and Drive cache duration non-negative')
        self._children_cache = {}
        self._listing_cache = {}
        self.creator = None
        self.transport = None

    def keep_alive(self):
        """Reuse one HTTPS connection for later reads and writes on this client."""
        if self.transport is None:
            self.transport = DriveTransport(self.timeout)
        return self.transport

    def _exchange(self, method, url, data=None, headers=None):
        if self.transport is None:
            request = urllib.request.Request(url, data=data, method=method)
            for name, value in (headers or {}).items():
                request.add_header(name, value)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        return self.transport.exchange(method, url, data, headers)

    def _forget_metadata(self):
        self._children_cache.clear()
        self._listing_cache.clear()

    def _request(self, method, url, payload=None, content_type="application/json"):
        headers = {"Authorization": "Bearer " + access_token_value(self.access_token)}
        if payload is not None:
            headers["Content-Type"] = content_type
        try:
            raw = self._exchange(method, url, payload, headers)
            return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError:
            self._forget_metadata()
            raise

    def _create_metadata(self, metadata):
        if self.creator is not None:
            return self.creator.create(metadata)
        return self._request(
            'POST', 'https://www.googleapis.com/drive/v3/files?fields=id,name,mimeType,parents,webViewLink',
            json.dumps(metadata).encode('utf-8'))

    def _replace_content(self, file_id, content, mime_type):
        self._listing_cache.clear()
        url = "https://www.googleapis.com/upload/drive/v3/files/{}?uploadType=media&fields=id,name,mimeType,parents,webViewLink".format(file_id)
        headers = {
            "Authorization": "Bearer " + access_token_value(self.access_token),
            "Content-Type": mime_type,
        }
        try:
            raw = self._exchange("PATCH", url, content, headers)
            return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError:
            self._forget_metadata()
            raise

    def _find_child(self, parent_id, name=None, mime_type=None, app_property=None):
        key = (parent_id, name, mime_type, app_property)
        cached = self._children_cache.get(key)
        if cached and monotonic() < cached[0]:
            return dict(cached[1])
        clauses = [
            "'{}' in parents".format(parent_id.replace("'", "\\'")),
            "trashed = false",
        ]
        if name is not None:
            clauses.append("name = '{}'".format(name.replace("'", "\\'")))
        if mime_type:
            clauses.append("mimeType = '{}'".format(mime_type))
        if app_property:
            key, value = app_property
            clauses.append(
                "appProperties has {{ key='{}' and value='{}' }}".format(
                    key.replace("'", "\\'"), value.replace("'", "\\'")))
        query = urllib.parse.quote(" and ".join(clauses), safe="")
        url = "https://www.googleapis.com/drive/v3/files?q={}&fields=files(id,name,mimeType,parents,webViewLink)".format(query)
        result = self._request("GET", url)
        files = result.get("files") or []
        if files:
            self._children_cache[key] = (monotonic() + self.metadata_cache_seconds, dict(files[0]))
            return files[0]
        # Never cache absence: a newly written receipt must immediately prevent
        # another submission, even when its first lookup returned not-found.
        self._children_cache.pop(key, None)
        return None

    def _list_children(self, parent_id):
        cached = self._listing_cache.get(parent_id)
        if cached and monotonic() < cached[0]:
            return [dict(item) for item in cached[1]]
        query = urllib.parse.quote(
            "'{}' in parents and trashed = false".format(parent_id.replace("'", "\\'")),
            safe="",
        )
        url = "https://www.googleapis.com/drive/v3/files?q={}&pageSize=1000&fields=files(id,name,mimeType,parents,webViewLink,modifiedTime)".format(query)
        files = self._request("GET", url).get("files") or []
        self._listing_cache[parent_id] = (monotonic() + min(5, self.metadata_cache_seconds),
                                          [dict(item) for item in files])
        return files

    def _ensure_folder(self, parent_id, name):
        existing = self._find_child(parent_id, name, FOLDER_MIME)
        if existing:
            return existing
        folder = self._create_metadata({
            'name': name, 'mimeType': FOLDER_MIME, 'parents': [parent_id],
        })
        self._children_cache[(parent_id, name, FOLDER_MIME, None)] = (
            monotonic() + self.metadata_cache_seconds, dict(folder))
        self._listing_cache.pop(parent_id, None)
        return folder

    def list_root_files(self):
        """List direct children of the configured Drive directory."""
        return self._list_children(self.root_folder_id)

    def ensure_application_directory(self):
        """Create the standard application-artifact directory when missing."""
        return self._ensure_folder(self.root_folder_id, self.application_root_path)

    def create_google_doc(self, name):
        """Create a native Google Doc directly inside the configured directory."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Google Doc name must be nonempty')
        return self._create_metadata({
            'name': name.strip(), 'mimeType': GOOGLE_DOC_MIME,
            'parents': [self.root_folder_id],
        })

    def save_path(self, path, content, mime_type=None, idempotency_key=None):
        normalized = normalize_file_path(path)
        parts = normalized.split("/")
        parent_id = self.root_folder_id
        for folder_name in parts[:-1]:
            parent_id = self._ensure_folder(parent_id, safe_folder_name(folder_name))["id"]
        filename = parts[-1]
        app_properties = {"idempotency_key": idempotency_key} if idempotency_key else None
        if idempotency_key:
            existing = self._find_child(parent_id, app_property=("idempotency_key", idempotency_key))
            if existing:
                payload = content.encode("utf-8") if isinstance(content, str) else content
                return self._replace_content(
                    existing["id"], payload,
                    mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                )
        existing = self._find_child(parent_id, filename)
        if existing:
            payload = content.encode('utf-8') if isinstance(content, str) else content
            return self._replace_content(existing['id'], payload,
                                         mime_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream')
        return self._save_in_folder(parent_id, filename, content, mime_type, app_properties)

    def stat_id(self, file_id):
        """File metadata for one Drive id. This does not download the file."""
        quoted = urllib.parse.quote(str(file_id), safe="")
        url = "https://www.googleapis.com/drive/v3/files/{}?fields=id,name,mimeType".format(quoted)
        return self._request("GET", url)

    def stat_path(self, path):
        """File metadata for a path under the Drive root. A missing file raises KeyError."""
        normalized = normalize_file_path(path)
        parts = normalized.split("/")
        parent_id = self.root_folder_id
        for folder_name in parts[:-1]:
            folder = self._find_child(parent_id, safe_folder_name(folder_name), FOLDER_MIME)
            if not folder:
                raise KeyError(normalized)
            parent_id = folder["id"]
        file = self._find_child(parent_id, parts[-1])
        if not file:
            raise KeyError(normalized)
        return file

    def read_path(self, path):
        file = self.stat_path(path)
        return {"file": file, "content": self.read(file["id"])}

    def _save_in_folder(self, folder_id, filename, content, mime_type=None, app_properties=None):
        self._listing_cache.pop(folder_id, None)
        mime_type = mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        metadata_value = {"name": filename, "parents": [folder_id]}
        if app_properties:
            metadata_value["appProperties"] = app_properties
        if isinstance(content, str):
            content = content.encode("utf-8")
        # Create metadata first, then upload bytes with the media endpoint.
        # This avoids fragile multipart boundary handling and makes the two
        # phases independently observable when Drive rejects an upload.
        metadata = self._create_metadata(dict(metadata_value, mimeType=mime_type))
        stored = self._replace_content(metadata["id"], content, mime_type)
        self._children_cache[(folder_id, filename, None, None)] = (
            monotonic() + self.metadata_cache_seconds, dict(stored))
        return stored

    def read(self, file_id):
        url = "https://www.googleapis.com/drive/v3/files/{}?alt=media".format(file_id)
        headers = {"Authorization": "Bearer " + access_token_value(self.access_token)}
        return self._exchange("GET", url, None, headers)

