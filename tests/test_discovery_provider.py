from adapters.job_boards.interface import AdapterResult
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


def test_read_posting_passes_the_url_without_choosing_an_agent(monkeypatch):
    seen = {}

    class Discovery:
        def generate_with_schema(self, prompt, context, schema):
            seen["context"] = context
            return {"company": "Acme", "role": "Backend", "posting_url": "https://example.test/job",
                    "description": "Backend role description. " * 100}

    monkeypatch.setattr("services.worker_main.load_discovery_provider", lambda: Discovery())
    agents = PipelineAgents(None, None, lambda p, c: AssertionError("pipeline provider must not read"))
    context = {"read_posting_url": "https://example.test/job"}
    result = agents.invoke("read-posting", context, "read-posting")
    assert seen["context"]["read_posting_url"] == "https://example.test/job"


def test_read_posting_uses_the_discovery_provider(monkeypatch):
    class Discovery:
        def generate_with_schema(self, prompt, context, schema):
            assert "company" in schema["properties"]
            return {"company": "Acme", "role": "Backend", "posting_url": "https://example.test/job",
                    "description": "Backend role description. " * 100}

    monkeypatch.setattr("services.worker_main.load_discovery_provider", lambda: Discovery())
    agents = PipelineAgents(None, None, lambda p, c: AssertionError("pipeline provider must not read"))
    result = agents.invoke("read-posting", {"read_posting_url": "https://example.test/job"}, "read-posting")
    assert result["company"] == "Acme"


def test_preparation_keeps_its_own_provider():
    def pipeline_provider(prompt, context):
        return {"cover_letter": "Dear team", "proposed_action": "Proceed"}

    class Discovery:
        def generate_with_schema(self, prompt, context, schema):
            raise AssertionError("discovery provider must not write the application")

    agents = PipelineAgents(None, None, pipeline_provider, discovery_provider=Discovery())
    result = agents.invoke("application", {"company": "Example"}, "prepare")
    assert result["proposed_action"] == "Proceed"


def test_empty_search_terms_are_derived_once(monkeypatch, tmp_path):
    from application_service.repository import MemoryRepository
    from services.orchestrator import Orchestrator
    from services.workload_registry import WorkloadRegistry
    from tests.test_orchestrator import pipeline_file

    calls = []

    class Discovery:
        def generate_with_schema(self, prompt, context, schema):
            calls.append(schema["required"])
            return {"search_terms": ["backend"]}

    class Board:
        def search(self, context, source):
            assert context.query == "backend"
            return AdapterResult(True, [])

    monkeypatch.setattr("adapters.job_boards.framework.get_framework", Board)
    repository = MemoryRepository()
    repository.profile = {"job_search": {"countries": ["IL"], "search_terms": []}}
    agents = PipelineAgents(repository, None, lambda prompt, context: pytest.fail("pipeline provider"), discovery_provider=Discovery())
    orchestrator = Orchestrator(repository, WorkloadRegistry(), agents, str(pipeline_file(tmp_path, read_posting=1)))
    try:
        assert orchestrator._collect_postings(repository.profile) == []
    finally:
        orchestrator.close()
    assert calls == [["search_terms"]]


def test_jobicy_searches_individual_terms_and_deduplicates(monkeypatch):
    from services.stage_executor import StageExecutor
    calls = []
    class Board:
        def search(self, context, source):
            assert source == 'jobicy'
            calls.append(context)
            return AdapterResult(True, [{'posting_url': 'https://jobicy.com/jobs/one', 'company': 'Example'}])
    monkeypatch.setattr('adapters.job_boards.framework.get_framework', Board)
    terms = ['Senior Backend Software Engineer', 'Distributed Systems Engineer']
    assert len(' OR '.join(terms)) > 50
    context = {'source_names': ['jobicy'], 'search_terms': terms, 'countries': ['IL']}
    StageExecutor._enrich_discovery_context(context)
    assert [call.query for call in calls] == terms
    assert len(context['job_postings']) == 1
