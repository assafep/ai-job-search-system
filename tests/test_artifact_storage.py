from persistence_api.repository import MemoryRepository


def test_memory_artifact_storage_keeps_bytes_and_metadata():
    repository = MemoryRepository()
    application = repository.create_application("Example", "Backend Engineer", "https://example.test/job")

    artifact = repository.store_artifact(
        application.application_id,
        "application.pdf",
        b"pdf",
        "application/pdf",
        "artifact-attempt-1",
    )
    retry = repository.store_artifact(
        application.application_id,
        "application.pdf",
        b"different-bytes",
        "application/pdf",
        "artifact-attempt-1",
    )

    assert artifact.name == "application.pdf"
    assert artifact.mime_type == "application/pdf"
    assert retry.artifact_id == artifact.artifact_id
    assert repository.artifact_contents[artifact.artifact_id] == b"pdf"


def test_memory_research_write_is_idempotent():
    repository = MemoryRepository()
    application = repository.create_application("Example", "Backend Engineer", "https://example.test/job")
    first = repository.write_research(application.application_id, "Research — Interview Briefing", "Scale?", "research-1")
    retry = repository.write_research(application.application_id, "Research — Interview Briefing", "Different", "research-1")
    assert retry == first


def test_memory_application_creation_deduplicates_posting_url():
    repository = MemoryRepository()
    first = repository.create_application("Example", "Backend", "https://example.test/job")
    retry = repository.create_application("Example", "Backend", "https://example.test/job")
    assert retry.application_id == first.application_id
    assert len(repository.applications) == 1


def test_memory_application_creation_deduplicates_tracking_url_variant():
    repository = MemoryRepository()
    first = repository.create_application(
        "Example", "Backend", "https://example.test/job/?utm_source=board"
    )
    retry = repository.create_application("Example", "Backend", "https://EXAMPLE.TEST/job")

    assert retry.application_id == first.application_id
    assert first.posting_url == "https://example.test/job"


def test_discovery_metadata_is_preserved_in_creation_event():
    repository = MemoryRepository()
    application = repository.create_application(
        "Example",
        "Backend",
        "https://example.test/job",
        {"relevance_evidence": ["senior", "backend"]},
    )
    event = repository.list_events(application.application_id)[0]
    assert "Posting URL: https://example.test/job" in event.data["bullets"]
    assert "Relevance: senior" in event.data["bullets"]


def test_memory_root_relative_file_storage_is_safe_and_idempotent():
    repository = MemoryRepository()
    first = repository.store_file("research/company/notes.md", b"notes", "text/markdown", "file-1")
    retry = repository.store_file("research/company/notes.md", b"changed", "text/markdown", "file-1")

    assert retry == first
    result = repository.read_file("research/company/notes.md")
    assert result["file"]["path"] == "research/company/notes.md"
    assert result["content"] == b"notes"


def test_memory_root_relative_file_storage_rejects_escape():
    repository = MemoryRepository()
    for path in ("/absolute.txt", "../outside.txt", "nested/../../outside.txt"):
        try:
            repository.store_file(path, b"x")
        except ValueError:
            continue
        raise AssertionError("unsafe path was accepted: " + path)
