from services.workflow_runtime import WorkflowRuntime


def test_adzuna_source_is_not_fetched_without_explicit_source(monkeypatch):
    called = []

    class Board:
        def __init__(self, boards, opener=None, timeout=30):
            pass

        def list_jobs(self):
            return []

    def unexpected(*args, **kwargs):
        called.append(True)
        raise AssertionError("source should not be fetched")

    monkeypatch.setattr("adapters.job_boards.greenhouse.GreenhouseJobBoard", Board)
    monkeypatch.setattr("adapters.job_boards.adzuna.AdzunaJobBoard", unexpected)
    context = {"source_names": ["greenhouse"]}
    WorkflowRuntime._enrich_discovery_context(context)
    assert called == []


def test_adzuna_source_fetches_postings(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def search(self, **kwargs):
            assert kwargs["what"] == "python OR backend"
            assert kwargs["where"] == "Tel Aviv"
            return [{"company": "Example", "role": "Engineer", "posting_url": "https://example"}]

    monkeypatch.setattr(
        "adapters.job_boards.adzuna.AdzunaJobBoard", Client
    )
    context = {
        "source_names": ["adzuna"],
        "search_terms": ["python", "backend"],
        "locations": ["Tel Aviv"],
    }
    WorkflowRuntime._enrich_discovery_context(context)
    assert context["job_postings"][0]["company"] == "Example"


def test_discovery_uses_onboarded_profile_countries(monkeypatch):
    class Client:
        def search(self, **kwargs):
            assert kwargs["countries"] == ["IL", "US"]
            assert kwargs["query"] == ""
            return []

    monkeypatch.setattr("adapters.job_boards.freehire.FreehireJobBoard", Client)
    context = {"source_names": ["freehire"]}
    profile = {"job_search": {"countries": ["IL", "US"], "search_terms": []}}
    WorkflowRuntime._enrich_discovery_context(context, profile)
    assert context["job_postings"] == []
