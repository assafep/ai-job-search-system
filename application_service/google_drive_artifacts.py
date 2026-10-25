"""Google Drive artifact storage for application folders."""

import json
import os
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from time import monotonic

from .google_auth import access_token_value
from .file_paths import normalize_file_path


FOLDER_MIME = "application/vnd.google-apps.folder"


def folder_id_from_url(value):
    # Drive emits both /drive/folders/<id> and /drive/u/0/folders/<id> links.
    match = re.search(r"/drive/(?:u/\d+/)?folders/([^/?]+)", value or "")
    return match.group(1) if match else value


def safe_folder_name(value):
    value = re.sub(r"[\\:*?\"<>|]+", "_", (value or "").strip())
    return re.sub(r"\s+", " ", value).lstrip(' ').rstrip(' .') or "Unnamed"


APPLICATIONS_DIRECTORY = "Applications"


def application_folder_name(company, role):
    """Return the repository's single-level ``Company / Role`` folder name."""
    label = " / ".join(part.strip() for part in (company, role) if part and part.strip())
    # A slash is intentional here: it is part of the existing Drive folder name
    # convention and does not create another folder in Google Drive.
    return safe_folder_name(label)


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
        from .google_auth import ServiceAccountToken, GoogleAccessToken
        if isinstance(access_token, ServiceAccountToken) and os.environ.get('AJSS_DATABASE_URL'):
            from .user_drive import file_credentials
            credentials = file_credentials()
            if credentials:
                from .drive_file_creator import FileCreator
                client_id, client_secret, refresh_token = credentials
                self.creator = FileCreator(self, GoogleAccessToken(refresh_token, client_id, client_secret))

    def _forget_metadata(self):
        self._children_cache.clear()
        self._listing_cache.clear()

    def _request(self, method, url, payload=None, content_type="application/json"):
        request = urllib.request.Request(url, method=method)
        request.add_header("Authorization", "Bearer " + access_token_value(self.access_token))
        if payload is not None:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, data=payload, timeout=self.timeout) as response:
                raw = response.read()
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
        request = urllib.request.Request(
            "https://www.googleapis.com/upload/drive/v3/files/{}?uploadType=media&fields=id,name,mimeType,parents,webViewLink".format(file_id),
            method="PATCH",
            data=content,
        )
        request.add_header("Authorization", "Bearer " + access_token_value(self.access_token))
        request.add_header("Content-Type", mime_type)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError:
            self._forget_metadata()
            raise

    def _rename(self, file_id, name):
        self._forget_metadata()
        return self._request(
            "PATCH",
            "https://www.googleapis.com/drive/v3/files/{}?fields=id,name,mimeType,parents,webViewLink".format(file_id),
            json.dumps({"name": name}).encode("utf-8"),
        )

    def _move(self, file_id, old_parent, new_parent):
        self._forget_metadata()
        query = urllib.parse.urlencode({'addParents': new_parent, 'removeParents': old_parent,
                                        'fields': 'id,name,mimeType,parents'})
        return self._request('PATCH', 'https://www.googleapis.com/drive/v3/files/{}?{}'.format(file_id, query), b'{}')

    def migrate_profile(self):
        from .storage_layout import APPLICANT_DIRECTORY, SYSTEM_DIRECTORY
        folder = self._ensure_folder(self.root_folder_id, APPLICANT_DIRECTORY)
        canonical = self._find_child(folder['id'], 'applicant_profile.json')
        if canonical is None:
            for name in ('applicant_profile.json', 'application_profile.json'):
                existing = self._find_child(self.root_folder_id, name)
                if existing:
                    self._move(existing['id'], self.root_folder_id, folder['id'])
                    if name != 'applicant_profile.json':
                        self._rename(existing['id'], 'applicant_profile.json')
                    return existing['id']
        if canonical:
            return canonical['id']
        legacy_folder = self._find_child(self.root_folder_id, SYSTEM_DIRECTORY, FOLDER_MIME)
        if legacy_folder:
            existing = self._find_child(legacy_folder['id'], 'applicant_profile.json')
            if existing:
                self._move(existing['id'], legacy_folder['id'], folder['id'])
                return existing['id']
        return None

    def migrate_internal_storage(self):
        from .storage_layout import SYSTEM_DIRECTORY
        profile_id = self.migrate_profile()
        system = self._ensure_folder(self.root_folder_id, SYSTEM_DIRECTORY)
        states = self._ensure_folder(system['id'], 'workloads-internal-state')
        legacy = self._find_child(self.root_folder_id, 'submission-receipts', FOLDER_MIME)
        moved = False
        archived = 0
        if legacy:
            workload = self._ensure_folder(states['id'], 'submit')
            destination = self._find_child(workload['id'], 'submission-receipts', FOLDER_MIME)
            if destination and destination['id'] != legacy['id']:
                raise ValueError('both legacy and new receipt folders exist; refusing an ambiguous merge')
            self._move(legacy['id'], self.root_folder_id, workload['id'])
            moved = True
            destination = legacy
        else:
            workload = self._find_child(states['id'], 'submit', FOLDER_MIME)
            destination = self._find_child(workload['id'], 'submission-receipts', FOLDER_MIME) if workload else None
        if destination:
            groups = {}
            for item in self._list_children(destination['id']):
                if item.get('mimeType') != FOLDER_MIME:
                    groups.setdefault(item['name'], []).append(item)
            for name, items in groups.items():
                if len(items) < 2:
                    continue
                # Older duplicate writes are preserved as history. Only the
                # newest receipt remains at the active path.
                items.sort(key=lambda item: (item.get('modifiedTime', ''), item['id']), reverse=True)
                history = self._ensure_folder(workload['id'], 'receipt-history')
                for item in items[1:]:
                    self._move(item['id'], destination['id'], history['id'])
                    stem = name[:-5] if name.endswith('.json') else name
                    self._rename(item['id'], '{}-{}.json'.format(stem, item['id']))
                    archived += 1
        return {'profile_file_id': profile_id, 'receipts_folder_moved': moved,
                'older_receipts_archived': archived, 'system_folder_id': system['id']}

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

    def find_tagged(self, application_id, reason):
        query = urllib.parse.quote(
            "appProperties has {{ key='ajss_completed_application' and value='{}' }} and trashed = false".format(
                application_id.replace("'", "\\'")), safe="")
        url = "https://www.googleapis.com/drive/v3/files?q={}&fields=files(id,name,appProperties)".format(query)
        for item in self._request("GET", url).get("files") or []:
            if (item.get("appProperties") or {}).get("ajss_completion_reason") == reason:
                return item
        return None

    def create_completed_document(self, application_id, reason, title, first_tab, journal_text, research, parent_id):
        if not parent_id:
            raise ValueError("a completed document must be created inside a shared Drive folder")
        # A native Google Doc is billed to the service account, which has no
        # Drive quota. A normal file created in the shared folder uses the
        # folder owner's quota. Research notes follow the journal text.
        sections = [journal_text or first_tab]
        for item in research or []:
            sections.append("{}\n{}".format(item.get("title") or "Research", item.get("content") or ""))
        created = self._create_metadata({
            'name': title, 'mimeType': 'text/plain', 'parents': [parent_id],
            'appProperties': {'ajss_completed_application': application_id,
                              'ajss_completion_reason': reason, 'ajss_first_tab': first_tab},
        })
        stored = self._replace_content(created["id"], "\n\n".join(sections).encode("utf-8"), "text/plain")
        stored["first_tab"] = first_tab
        return stored

    def place_document(self, file_id, company, role):
        folder = self.application_folder(company, role)
        meta = self._request("GET", "https://www.googleapis.com/drive/v3/files/{}?fields=parents".format(file_id))
        parents = meta.get("parents") or []
        if parents == [folder["id"]]:
            return meta
        query = "addParents={}".format(folder["id"])
        if parents:
            query += "&removeParents=" + ",".join(parents)
        return self._request("PATCH", "https://www.googleapis.com/drive/v3/files/{}?{}".format(file_id, query))

    def migrate_application_folders(self):
        """Move Company / Role folders from the Drive root into Applications."""
        applications = self._ensure_folder(self.root_folder_id, APPLICATIONS_DIRECTORY)
        moved = []
        for item in self._list_children(self.root_folder_id):
            name = item.get("name") or ""
            if item.get("mimeType") != FOLDER_MIME or name in {APPLICATIONS_DIRECTORY, ".ai-job-search-system"}:
                continue
            if " / " not in name:
                continue
            self._move(item["id"], self.root_folder_id, applications["id"])
            moved.append(name)
        return moved

    def app_owned_folder(self, name='AJSS Completed'):
        """Folder this app created. drive.file can see it and cannot see the rest of Drive."""
        query = urllib.parse.quote(
            "trashed = false and mimeType = 'application/vnd.google-apps.folder' "
            "and appProperties has { key='ajss_kind' and value='completed_root' }",
            safe='')
        listed = self._request(
            'GET', 'https://www.googleapis.com/drive/v3/files?q={}&fields=files(id,name)'.format(query))
        files = listed.get('files') or []
        if files:
            return files[0]
        return self._request(
            'POST', 'https://www.googleapis.com/drive/v3/files?fields=id,name',
            json.dumps({
                'name': name,
                'mimeType': FOLDER_MIME,
                'parents': ['root'],
                'appProperties': {'ajss_kind': 'completed_root'},
            }).encode('utf-8'))

    def applications_directory(self):
        """Folder the service account can write. A file with no parent uses the account's own Drive, which has no storage quota."""
        parent_id = self.root_folder_id
        if self.application_root_path:
            for folder_name in self.application_root_path.split("/"):
                parent_id = self._ensure_folder(parent_id, safe_folder_name(folder_name))["id"]
        return parent_id

    def application_folder(self, company, role):
        return self._ensure_folder(
            self.applications_directory(),
            application_folder_name(company, role),
        )

    def existing_application_folder(self, company, role):
        parent_id = self.root_folder_id
        if self.application_root_path:
            for folder_name in self.application_root_path.split("/"):
                folder = self._find_child(parent_id, safe_folder_name(folder_name), FOLDER_MIME)
                if not folder:
                    return None
                parent_id = folder["id"]
        return self._find_child(parent_id, application_folder_name(company, role), FOLDER_MIME)

    def list_application_files(self, company, role):
        folder = self.existing_application_folder(company, role)
        return self._list_children(folder["id"]) if folder else []

    def save(self, company, role, filename, content, mime_type=None, idempotency_key=None):
        # Writes reconcile fresh file lists; only folder identities are reused.
        self._listing_cache.clear()
        folder = self.application_folder(company, role)
        # Reconcile the existing combined package across filename migrations.
        # Replacing it preserves the file ID linked from the journal.
        if " Cover Letter & Resume - " in filename:
            packages = [item for item in self._list_children(folder["id"])
                        if item.get("name") == filename
                        or item.get("name") == "application-package.pdf"
                        or item.get("name", "").endswith(" - Application Package.pdf")]
            if len(packages) > 1:
                raise ValueError("multiple application packages found; replacement is ambiguous")
            if packages:
                self._rename(packages[0]["id"], filename)
                return self._replace_content(packages[0]["id"], content, mime_type or "application/pdf")
        if idempotency_key:
            existing = self._find_child(
                folder["id"], app_property=("idempotency_key", idempotency_key))
            if existing:
                content = content.encode("utf-8") if isinstance(content, str) else content
                if existing.get('name') and existing['name'] != filename:
                    self._rename(existing["id"], filename)
                return self._replace_content(
                    existing["id"],
                    content,
                    mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                )
        # Fallback: for posting artifacts, find by filename to avoid duplicates
        # when the idempotency key is missing or changed across runs.
        if filename.startswith("Job Posting - "):
            existing = self._find_child(folder["id"], filename)
            if existing:
                content = content.encode("utf-8") if isinstance(content, str) else content
                return self._replace_content(
                    existing["id"],
                    content,
                    mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                )
        app_properties = {"idempotency_key": idempotency_key} if idempotency_key else None
        return self._save_in_folder(folder["id"], filename, content, mime_type, app_properties)

    def save_root(self, filename, content, mime_type=None):
        existing = self._find_child(self.root_folder_id, filename)
        if existing:
            content = content.encode("utf-8") if isinstance(content, str) else content
            return self._replace_content(
                existing["id"],
                content,
                mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
            )
        return self._save_in_folder(self.root_folder_id, filename, content, mime_type)

    def read_root(self, filename):
        file = self._find_child(self.root_folder_id, filename)
        if not file:
            return None
        return self.read(file["id"])

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

    def read_path(self, path):
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
        request = urllib.request.Request(url, method="GET")
        request.add_header("Authorization", "Bearer " + access_token_value(self.access_token))
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()
