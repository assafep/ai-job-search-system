from application_service.repository import MemoryRepository
from application_service.google_docs_repository import artifact_reference


def test_memory_artifact_list_and_read_are_application_scoped():
    repository = MemoryRepository()
    first = repository.create_application("Example", "Backend", "https://example.test/one")
    second = repository.create_application("Other", "Backend", "https://example.test/two")
    artifact = repository.store_artifact(first.application_id, "resume.pdf", b"pdf", "application/pdf")
    repository.store_artifact(second.application_id, "other.pdf", b"other", "application/pdf")

    assert repository.list_artifacts(first.application_id) == [artifact]
    result = repository.read_artifact(first.application_id, artifact.artifact_id)
    assert result["content"] == b"pdf"


def test_google_artifact_parser_accepts_existing_journal_references():
    assert artifact_reference(
        "Application PDF: https://drive.google.com/file/d/drive-file-1/view"
    ) == ("Application PDF", "drive-file-1")
    assert artifact_reference("Applied Resume: resume.pdf") == ("Applied Resume", None)
