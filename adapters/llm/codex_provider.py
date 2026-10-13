"""Codex CLI provider for subscription-backed local agent execution."""

import json
import os
import subprocess
import tempfile
from copy import copy
from pathlib import Path


def strict_schema(schema):
    """Adapt the repository's permissive schema subset to Codex strict JSON output."""
    result = dict(schema)
    if "oneOf" in result:
        result["oneOf"] = [strict_schema(branch) for branch in result["oneOf"]]
    if result.get("type") == "object":
        properties = result.get("properties", {})
        result["properties"] = {
            name: strict_schema(definition) for name, definition in properties.items()
        }
        result["required"] = list(properties)
        result["additionalProperties"] = False
    elif result.get("type") == "array" and "items" in result:
        result["items"] = strict_schema(result["items"])
    return result


class CodexProvider:
    """Run one stateless agent through a logged-in ``codex exec`` process."""

    def __init__(self, executable=None, model=None, timeout=None, reasoning_effort=None):
        self.executable = executable or os.environ.get("CODEX_EXECUTABLE", "codex")
        self.model = model or os.environ.get("CODEX_MODEL")
        self.timeout = int(timeout or os.environ.get("CODEX_TIMEOUT_SECONDS", "300"))
        self.reasoning_effort = reasoning_effort or os.environ.get("CODEX_REASONING_EFFORT")

    def with_reasoning_effort(self, effort):
        """Return a workload-local provider without changing the shared instance."""
        configured = copy(self)
        configured.reasoning_effort = effort
        return configured

    def generate(self, prompt, context):
        raise RuntimeError("CodexProvider requires the agent schema through generate_with_schema")

    def generate_with_schema(self, prompt, context, schema):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(context, dict):
            raise ValueError("context must be an object")
        if not isinstance(schema, dict):
            raise ValueError("schema must be an object")

        request_text = "{}\n\nContext JSON:\n{}".format(
            prompt,
            json.dumps(context, ensure_ascii=False, sort_keys=True),
        )
        with tempfile.TemporaryDirectory(prefix="job-search-codex-") as directory:
            directory = Path(directory)
            schema_path = directory / "schema.json"
            output_path = directory / "result.json"
            schema_path.write_text(json.dumps(strict_schema(schema)), encoding="utf-8")
            command = [
                self.executable,
                "exec",
                "--sandbox", "read-only",
                "--ephemeral",
                "--output-schema", str(schema_path),
                "--output-last-message", str(output_path),
                "-",
            ]
            if self.model:
                command[2:2] = ["--model", self.model]
            if self.reasoning_effort:
                command[2:2] = ["--config", 'model_reasoning_effort={}'.format(json.dumps(self.reasoning_effort))]
            completed = subprocess.run(
                command,
                input=request_text,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()
                raise RuntimeError("Codex CLI failed ({}): {}".format(
                    completed.returncode, detail[-2000:]
                ))
            if not output_path.exists():
                raise RuntimeError("Codex CLI did not produce a structured result")
            try:
                return json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise RuntimeError("Codex CLI result was not valid JSON: {}".format(error))


provider = CodexProvider()
