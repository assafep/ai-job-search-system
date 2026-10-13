import pytest

from services.agent_runner import AgentContractError, contract, run


def test_agent_runner_loads_contract_and_validates_provider_result():
    calls = []

    def provider(prompt, context):
        calls.append((prompt, context))
        return '{"briefing": "Discuss reliability."}'

    result = run("interview-prep", {"company": "Example"}, provider)

    assert result["briefing"] == "Discuss reliability."
    assert calls[0][1] == {"company": "Example"}
    assert "briefing" in contract("interview-prep")["schema"]["required"]


def test_agent_runner_rejects_invalid_provider_output():
    with pytest.raises(AgentContractError, match="briefing"):
        run("interview-prep", {}, lambda prompt, context: {"wrong": "shape"})


def test_agent_runner_rejects_non_object_context():
    with pytest.raises(ValueError, match="context must be an object"):
        run("interview-prep", [], lambda prompt, context: {})


def test_agent_runner_accepts_object_provider_contract():
    class Provider:
        def generate(self, prompt, context):
            return {"briefing": "Discuss reliability."}

    assert run("interview-prep", {}, Provider())["briefing"] == "Discuss reliability."


def test_agent_runner_rejects_malformed_nested_candidate():
    with pytest.raises(AgentContractError, match="posting_url"):
        run(
            "job-discovery",
            {},
            lambda prompt, context: {"candidates": [{"company": "Example", "role": "Backend"}]},
        )


def test_codex_schema_selection_uses_application_batch_shape():
    class Provider:
        def __init__(self):
            self.schema = None

        def generate_with_schema(self, prompt, context, schema):
            self.schema = schema
            return {"packages": [{
                "posting_url": "https://example.test/job",
                "cover_letter": "Hello",
                "proposed_action": "bookmark",
            }]}

    provider = Provider()
    result = run(
        "application",
        {"discovered_candidates": [{"posting_url": "https://example.test/job"}]},
        provider,
    )
    assert "packages" in provider.schema["properties"]
    assert result["packages"][0]["proposed_action"] == "bookmark"
