import json

import pytest

from adapters.llm import coding_agent


def _fake_run(seen):
    def fake_run(command, input, capture_output, text, timeout, check, env):
        seen["command"] = command
        seen["input"] = input
        seen["env"] = env
        seen["timeout"] = timeout
        if "--prompt-file" in command:
            seen["prompt"] = open(command[command.index("--prompt-file") + 1], encoding="utf-8").read()
        payload = json.dumps({"structured_output": {"search_terms": ["backend"]}})
        if "--output-last-message" in command:
            with open(command[command.index('--output-schema') + 1], encoding='utf-8') as handle:
                seen['schema'] = json.load(handle)
            output_path = command[command.index("--output-last-message") + 1]
            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump({"candidates": []}, handle)
            payload = ""

        class Result:
            returncode = 0
            stdout = payload
            stderr = ""

        return Result()

    return fake_run


def test_scout_launcher_does_not_choose_a_model(monkeypatch):
    seen = {}
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    monkeypatch.delenv("CODING_AGENT_MODEL", raising=False)
    monkeypatch.setattr(coding_agent.subprocess, "run", _fake_run(seen))
    provider = coding_agent.CodingAgentProvider(executable="grok", timeout=12)
    assert provider.generate_with_schema(
        "Plan the search.", {"applicant_profile": {"name": "Ada"}},
        {"type": "object", "required": ["search_terms"]}) == {"search_terms": ["backend"]}
    assert seen["command"][0] == "grok"
    assert "--model" not in seen["command"]
    assert "--disable-web-search" in seen["command"]
    assert "XAI_API_KEY" not in seen["env"]
    assert seen["timeout"] == 12
    assert "Ada" in seen["prompt"]


def test_model_is_forwarded_only_when_configured(monkeypatch):
    seen = {}
    monkeypatch.setattr(coding_agent.subprocess, "run", _fake_run(seen))
    provider = coding_agent.CodingAgentProvider(executable="codex", model="named-model")
    assert provider.generate_with_schema("Read.", {"read_posting_url": "https://example.test/job"},
                                         {"type": "object"}) == {"candidates": []}
    assert seen["command"][:2] == ["codex", "exec"]
    assert seen["command"][seen["command"].index("--model") + 1] == "named-model"


def test_codex_scout_schema_is_strict_at_every_object(monkeypatch):
    seen = {}
    monkeypatch.setattr(coding_agent.subprocess, 'run', _fake_run(seen))
    schema = {'type': 'object', 'required': ['candidates'], 'properties': {
        'read_failure': {'type': 'string'},
        'candidates': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'description': {'type': 'string'}}}}}}
    coding_agent.CodingAgentProvider(executable='codex').generate_with_schema('Read.', {}, schema)
    assert seen['schema']['additionalProperties'] is False
    assert set(seen['schema']['required']) == {'candidates', 'read_failure'}
    candidate = seen['schema']['properties']['candidates']['items']
    assert candidate['additionalProperties'] is False
    assert candidate['required'] == ['description']
    assert 'additionalProperties' not in schema


def test_reading_a_url_asks_the_configured_agent_to_browse(monkeypatch):
    seen = {}
    monkeypatch.delenv("CODING_AGENT_REASONING", raising=False)
    monkeypatch.setattr(coding_agent.subprocess, "run", _fake_run(seen))
    provider = coding_agent.CodingAgentProvider(executable="grok")
    provider.generate_with_schema("Read it.", {"read_posting_url": "https://example.test/job"}, {"type": "object"})
    tools = seen["command"][seen["command"].index("--tools") + 1]
    assert tools == "web_search,web_fetch"
    assert seen["command"][seen["command"].index("--reasoning-effort") + 1] == "low"
    assert "--model" not in seen["command"]
    assert "--disable-web-search" not in seen["command"]


def test_concatenated_grok_objects_keep_the_read_posting():
    placeholder = '{"candidates":[],"read_failure":"I need to open the posting."}'
    final = '{"candidates":[{"description":"Full posting text.","posting_url":"https://example.test/job"}],"read_failure":""}'
    stdout = json.dumps({"text": placeholder + final})
    result = coding_agent._grok_result(stdout)
    assert result["candidates"][0]["description"] == "Full posting text."
    assert result["read_failure"] == ""


def test_unknown_coding_agent_is_rejected(monkeypatch):
    monkeypatch.setattr(coding_agent.subprocess, "run", lambda **kwargs: pytest.fail("must not launch"))
    provider = coding_agent.CodingAgentProvider(executable="some-other-agent")
    with pytest.raises(RuntimeError, match="No CLI profile"):
        provider.generate_with_schema("Plan.", {}, {"type": "object"})


def test_missing_coding_agent_is_rejected(monkeypatch):
    monkeypatch.delenv("CODING_AGENT", raising=False)
    provider = coding_agent.CodingAgentProvider()
    with pytest.raises(RuntimeError, match="scout.coding_agent"):
        provider.generate_with_schema("Plan.", {}, {"type": "object"})
