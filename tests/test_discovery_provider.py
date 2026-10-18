import pytest

from services.pipeline_agents import PipelineAgents


def test_scout_uses_persisted_command_when_environment_is_missing(monkeypatch):
    from services.worker_main import load_discovery_provider
    monkeypatch.delenv('CODING_AGENT', raising=False)
    monkeypatch.delenv('CODING_AGENT_MODEL', raising=False)
    monkeypatch.delenv('DISCOVERY_PROVIDER', raising=False)
    provider = load_discovery_provider(runtime_config={'scout': {'coding_agent': 'codex'}})
    assert provider._executable() == 'codex'
    assert provider._model() is None


def test_scout_environment_overrides_persisted_command(monkeypatch):
    from services.worker_main import load_discovery_provider
    monkeypatch.setenv('CODING_AGENT', 'grok')
    monkeypatch.delenv('DISCOVERY_PROVIDER', raising=False)
    provider = load_discovery_provider(runtime_config={'scout': {'coding_agent': 'codex'}})
    assert provider._executable() == 'grok'


def test_job_discovery_passes_the_url_without_choosing_an_agent():
    def pipeline_provider(prompt, context):
        raise AssertionError("preparation provider must not read postings")

    seen = {}

    class Discovery:
        def generate_with_schema(self, prompt, context, schema):
            seen["context"] = context
            return {"candidates": []}

    agents = PipelineAgents(None, None, pipeline_provider, discovery_provider=Discovery())
    context = {"read_posting_url": "https://example.test/job", "job_postings": []}
    assert agents.invoke("job-discovery", context, "discover-and-bookmark") == {"candidates": []}
    assert seen["context"]["read_posting_url"] == "https://example.test/job"


def test_job_discovery_uses_the_discovery_provider():
    def pipeline_provider(prompt, context):
        raise AssertionError("preparation provider must not score postings")

    class Discovery:
        def generate_with_schema(self, prompt, context, schema):
            assert "candidates" in schema["properties"]
            return {"candidates": []}

    agents = PipelineAgents(None, None, pipeline_provider, discovery_provider=Discovery())
    assert agents.invoke("job-discovery", {"job_postings": []}, "discover-and-bookmark") == {"candidates": []}


def test_preparation_keeps_its_own_provider():
    def pipeline_provider(prompt, context):
        return {"cover_letter": "Dear team", "proposed_action": "Proceed"}

    class Discovery:
        def generate_with_schema(self, prompt, context, schema):
            raise AssertionError("discovery provider must not write the application")

    agents = PipelineAgents(None, None, pipeline_provider, discovery_provider=Discovery())
    result = agents.invoke("application", {"company": "Example"}, "prepare-bookmarked")
    assert result["proposed_action"] == "Proceed"


def test_empty_search_terms_are_derived_once(monkeypatch, tmp_path):
    from persistence_api.repository import MemoryRepository
    from services.orchestrator import Orchestrator
    from services.workload_registry import WorkloadRegistry
    from tests.test_orchestrator import pipeline_file

    calls = []

    class Discovery:
        def generate_with_schema(self, prompt, context, schema):
            calls.append(schema["required"])
            return {"search_terms": ["backend"]}

    class Board:
        def search(self, **kwargs):
            assert kwargs["query"] == "backend"
            return []

    monkeypatch.setattr("adapters.job_boards.freehire.FreehireJobBoard", Board)
    monkeypatch.setattr("adapters.job_boards.jobicy.JobicyJobBoard", Board)
    repository = MemoryRepository()
    repository.profile = {"job_search": {"countries": ["IL"], "search_terms": []}}
    agents = PipelineAgents(repository, None, lambda prompt, context: pytest.fail("pipeline provider"), discovery_provider=Discovery())
    orchestrator = Orchestrator(repository, WorkloadRegistry(), agents, str(pipeline_file(tmp_path, scout=1)))
    try:
        assert orchestrator._collect_postings(repository.profile) == []
    finally:
        orchestrator.close()
    assert calls == [["search_terms"]]
