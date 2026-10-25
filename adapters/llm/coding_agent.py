"""Local coding-agent adapter for scout.

The scout pipeline calls ``generate_with_schema``. This module launches whatever
command ``CODING_AGENT`` names. It sends a model only when ``CODING_AGENT_MODEL``
is set.
"""

import json
import os
import subprocess
import tempfile
from copy import copy
from pathlib import Path
from .codex_provider import strict_schema


class CodingAgentProvider:
    """Run one structured scout call through a configured local coding agent."""

    def __init__(self, executable=None, model=None, timeout=None):
        self.executable = executable
        self.model = model
        self.reasoning_effort = None
        self.timeout = int(timeout or os.environ.get("CODING_AGENT_TIMEOUT_SECONDS") or os.environ.get("GROK_TIMEOUT_SECONDS") or "300")

    def with_reasoning_effort(self, effort):
        configured = copy(self)
        configured.reasoning_effort = effort
        return configured

    def generate(self, prompt, context):
        raise RuntimeError("CodingAgentProvider requires the agent schema through generate_with_schema")

    def generate_with_schema(self, prompt, context, schema):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(context, dict):
            raise ValueError("context must be an object")
        if not isinstance(schema, dict):
            raise ValueError("schema must be an object")
        executable = self._executable()
        kind = _kind(executable)
        model = self._model()
        request_text = "{}\n\nContext JSON:\n{}".format(
            prompt, json.dumps(context, ensure_ascii=False, sort_keys=True))
        env = os.environ.copy()
        env.pop("XAI_API_KEY", None)
        if kind == "grok":
            env["GROK_DISABLE_AUTOUPDATER"] = "1"
        read_url = bool(str(context.get("read_posting_url") or "").strip())
        with tempfile.TemporaryDirectory(prefix="job-search-agent-") as directory:
            directory = Path(directory)
            if kind == "grok":
                completed = _run_grok(
                    executable, model, request_text, schema, directory, read_url, env, self.timeout,
                    self.reasoning_effort)
                output_path = None
            else:
                completed, output_path = _run_codex(executable, model, request_text, schema, directory, env, self.timeout)
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip()
                raise RuntimeError("coding agent failed ({}): {}".format(completed.returncode, detail[-500:]))
            if kind == "codex":
                if output_path is None or not output_path.exists():
                    raise RuntimeError("coding agent did not produce a structured result")
                try:
                    result = json.loads(output_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as error:
                    raise RuntimeError("coding agent result was not valid JSON: {}".format(error))
                if not isinstance(result, dict):
                    raise RuntimeError("coding agent did not produce a structured result")
                return result
            return _grok_result(completed.stdout)

    def _executable(self):
        raw = self.executable or os.environ.get("CODING_AGENT")
        if not str(raw or "").strip():
            raise RuntimeError(
                "Scout coding agent is not configured. Set CODING_AGENT or scout.coding_agent in config/runtime.json to grok or codex.")
        return str(raw)

    def _model(self):
        raw = self.model if self.model is not None else os.environ.get("CODING_AGENT_MODEL")
        text = str(raw or "").strip()
        return text or None


def _kind(executable):
    name = Path(executable).name
    if name in {"grok", "codex"}:
        return name
    raise RuntimeError(
        "No CLI profile for coding agent {!r}. Set DISCOVERY_PROVIDER to a provider module. Scout does not choose a model.".format(name))


def _run_grok(executable, model, request_text, schema, directory, read_url, env, timeout, reasoning_effort=None):
    prompt_path = directory / "prompt.txt"
    prompt_path.write_text(request_text, encoding="utf-8")
    command = [executable]
    if model:
        command.extend(["--model", model])
    if read_url:
        # A page read should stay a short turn. The agent's high default spends
        # most of its time rewriting the posting. The bookmark workflow effort
        # overrides this, then CODING_AGENT_REASONING, then low.
        effort = str(reasoning_effort or env.get("CODING_AGENT_REASONING") or "low").strip()
        command.extend(["--tools", "web_search,web_fetch", "--always-approve", "--reasoning-effort", effort])
    else:
        command.append("--disable-web-search")
    command.extend([
        "--cwd", str(directory),
        "--verbatim",
        "--no-plan",
        "--no-subagents",
        "--no-auto-update",
        "--max-turns", "8",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--prompt-file", str(prompt_path),
    ])
    return _spawn(command, None, env, timeout)


def _run_codex(executable, model, request_text, schema, directory, env, timeout):
    schema_path = directory / "schema.json"
    output_path = directory / "result.json"
    schema_path.write_text(json.dumps(strict_schema(schema)), encoding="utf-8")
    command = [executable, "exec", "--sandbox", "read-only", "--ephemeral"]
    if model:
        command.extend(["--model", model])
    command.extend([
        "--output-schema", str(schema_path),
        "--output-last-message", str(output_path),
        "-",
    ])
    return _spawn(command, request_text, env, timeout), output_path


def _spawn(command, request_text, env, timeout):
    try:
        return subprocess.run(
            command, input=request_text, capture_output=True, text=True,
            timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError("coding agent timed out after {} seconds".format(timeout))


def _grok_result(stdout):
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("coding agent result was not valid JSON: {}".format(error))
    if not isinstance(payload, dict):
        raise RuntimeError("coding agent did not produce a structured result")
    found = []
    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        found.append(structured)
    text = payload.get("text")
    if isinstance(text, str):
        found.extend(item for item in _json_values(text) if isinstance(item, dict))
    chosen = _choose_result(found)
    if chosen is None:
        raise RuntimeError("coding agent did not produce a structured result")
    return chosen


def _json_values(text):
    """Return every JSON value in text. A tool turn can emit more than one object."""
    decoder = json.JSONDecoder()
    index = 0
    found = []
    length = len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        found.append(value)
        index = end
    return found


def _choose_result(objects):
    """Keep the last object that contains the page. An earlier object may be a placeholder."""
    for item in reversed(objects):
        candidates = item.get("candidates") if isinstance(item, dict) else None
        if not isinstance(candidates, list):
            continue
        if any(isinstance(candidate, dict) and str(candidate.get("description") or "").strip() for candidate in candidates):
            return item
    for item in reversed(objects):
        candidates = item.get("candidates") if isinstance(item, dict) else None
        if isinstance(candidates, list) and candidates:
            return item
    return objects[-1] if objects else None


provider = CodingAgentProvider()
