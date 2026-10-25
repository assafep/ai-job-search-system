import json

from application_service.google_drive_artifacts import (
    GoogleDriveArtifactStore,
    application_folder_name,
    folder_id_from_url,
    safe_folder_name,
)
from application_service.google_docs_repository import GoogleDocsRepository


def test_folder_id_is_resolved_from_drive_url():
    assert folder_id_from_url("https://drive.google.com/drive/folders/root-123?usp=sharing") == "root-123"
    assert folder_id_from_url("https://drive.google.com/drive/u/0/folders/root-123?usp=sharing") == "root-123"


def test_folder_names_are_safe_and_stable():
    assert safe_folder_name("Example / Systems") == "Example / Systems"
    assert safe_folder_name("Senior   Backend   Engineer") == "Senior Backend Engineer"


def test_application_folder_is_one_combined_folder_name():
    assert application_folder_name("Example", "Senior Backend Engineer") == (
        "Example / Senior Backend Engineer"
    )


def test_drive_store_retries_update_file_marked_with_idempotency_key():
    class FakeStore(GoogleDriveArtifactStore):
        def __init__(self):
            super().__init__("token", "root")
            self.replaced = []

        def application_folder(self, company, role):
            return {"id": "application-folder"}

        def _find_child(self, parent_id, name=None, mime_type=None, app_property=None):
            assert parent_id == "application-folder"
            assert app_property == ("idempotency_key", "attempt-1")
            return {"id": "drive-file-1"}

        def _replace_content(self, file_id, content, mime_type):
            self.replaced.append((file_id, content, mime_type))
            return {"id": file_id, "mimeType": mime_type}

        def _save_in_folder(self, *args):
            raise AssertionError("retry should not create another file")

    store = FakeStore()
    result = store.save("Example", "Backend", "application.pdf", b"new", "application/pdf", "attempt-1")
    assert result["id"] == "drive-file-1"
    assert store.replaced == [("drive-file-1", b"new", "application/pdf")]


def test_completed_document_is_created_in_the_position_folder():
    class RecordingStore(GoogleDriveArtifactStore):
        def __init__(self):
            super().__init__("token", "root", "Applications")
            self.posts = []

        def _ensure_folder(self, parent_id, name):
            return {"id": "applications" if name == "Applications" else "position"}

        def _request(self, method, url, payload=None, content_type="application/json"):
            self.posts.append((method, url, payload))
            return {"id": "doc-1", "name": "Completed", "parents": ["position"]}

        def _replace_content(self, file_id, content, mime_type):
            self.posts.append(("content", file_id, content, mime_type))
            return {"id": file_id, "name": "Completed"}

    store = RecordingStore()
    from application_service.completion_drive import DriveCompletion
    DriveCompletion(store).create_completion(
        "app-1", "Invalidated", "Completed (Invalidated) Journal - DealHub / Staff",
        "declined", [], {"company": "DealHub", "role": "Staff Software Engineer"})
    body = json.loads(store.posts[0][2].decode())
    assert body["parents"] == ["position"]
    assert body["mimeType"] == "text/plain"


def test_application_folders_can_be_nested_under_configured_parent():
    class NestedStore(GoogleDriveArtifactStore):
        def __init__(self):
            super().__init__("token", "root", "applications")
            self.created = []

        def _ensure_folder(self, parent_id, name):
            self.created.append((parent_id, name))
            return {"id": "nested-parent" if name == "applications" else "application-folder"}

    store = NestedStore()
    assert store.application_folder("Example", "Backend")["id"] == "application-folder"
    assert store.created == [
        ("root", "applications"),
        ("nested-parent", "Example / Backend"),
    ]


def test_google_repository_normalizes_generic_file_metadata():
    class FakeArtifacts:
        def save_path(self, path, content, mime_type, idempotency_key):
            return {"id": "drive-file-1", "name": "notes.md", "mimeType": mime_type}

        def read_path(self, path):
            return {"file": {"id": "drive-file-1", "name": "notes.md", "mimeType": "text/markdown"}, "content": b"notes"}

    repository = GoogleDocsRepository("token", "document", "https://drive.google.com/drive/folders/root")
    repository.artifacts = FakeArtifacts()

    assert repository.store_file("shared/notes.md", b"notes", "text/markdown") == {
        "file_id": "drive-file-1",
        "path": "shared/notes.md",
        "name": "notes.md",
        "mime_type": "text/markdown",
    }
    assert repository.read_file("shared/notes.md")["file"]["file_id"] == "drive-file-1"
