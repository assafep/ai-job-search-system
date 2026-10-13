import json
import pytest

from adapters.llm import codex_provider


@pytest.mark.parametrize('effort', [None, 'medium', 'high'])
def test_codex_provider_uses_schema_and_reads_structured_result(monkeypatch, effort):
    seen = {}

    def fake_run(command, input, capture_output, text, timeout, check):
        seen["command"] = command
        seen["input"] = input
        output_path = command[command.index("--output-last-message") + 1]
        with open(output_path, "w") as handle:
            json.dump({"candidates": []}, handle)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(codex_provider.subprocess, "run", fake_run)
    monkeypatch.delenv('CODEX_REASONING_EFFORT', raising=False)
    provider = codex_provider.CodexProvider(executable="codex-test", reasoning_effort=effort)
    result = provider.generate_with_schema(
        "Find candidates.", {"source": "fixture"},
        {"type": "object", "required": ["candidates"]},
    )

    assert result == {"candidates": []}
    assert seen["command"][0:2] == ["codex-test", "exec"]
    if effort:
        assert seen['command'][2:4] == ['--config', 'model_reasoning_effort="{}"'.format(effort)]
    else:
        assert seen['command'][2] == '--sandbox'
    assert "Find candidates." in seen["input"]
    assert "--output-schema" in seen["command"]
