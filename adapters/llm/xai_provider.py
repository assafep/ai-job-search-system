"""xAI Responses API adapter. Discovery uses this provider for Grok."""

import json
import os
import urllib.error
import urllib.request
from copy import copy

from adapters.llm.codex_provider import strict_schema


def _safe_error_detail(detail, secret):
    if secret:
        detail = detail.replace(secret, "[redacted]")
    return detail[-500:]


class XAIProvider:
    """Stateless Grok client. It receives prompt text and context only."""

    def __init__(self, api_key=None, model=None, endpoint=None, timeout=None, opener=None):
        self.api_key = api_key or os.environ.get("XAI_API_KEY")
        self.model = model or os.environ.get("XAI_MODEL", "grok-4.7")
        self.endpoint = endpoint or os.environ.get("XAI_RESPONSES_URL", "https://api.x.ai/v1/responses")
        self.timeout = int(timeout or os.environ.get("XAI_TIMEOUT_SECONDS", "300"))
        self.opener = opener or urllib.request.urlopen
        self.tools = None

    def with_tools(self, tools):
        """Return a call-local provider that may browse while reading a posting."""
        configured = copy(self)
        configured.tools = list(tools)
        return configured

    def generate(self, prompt, context):
        return self._complete(prompt, context, {"type": "json_object"})

    def generate_with_schema(self, prompt, context, schema):
        if not isinstance(schema, dict):
            raise ValueError("schema must be an object")
        return self._complete(prompt, context, {
            "type": "json_schema",
            "name": "result",
            "schema": strict_schema(schema),
            "strict": True,
        })

    def _complete(self, prompt, context, text_format):
        if not self.api_key:
            raise RuntimeError("XAI_API_KEY is not configured")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(context, dict):
            raise ValueError("context must be an object")
        payload = {
            "model": self.model,
            "input": "{}\n\nContext JSON:\n{}".format(
                prompt, json.dumps(context, ensure_ascii=False, sort_keys=True)),
            "text": {"format": text_format},
        }
        if self.tools:
            payload["tools"] = self.tools
        request = urllib.request.Request(
            self.endpoint, method="POST", data=json.dumps(payload).encode("utf-8"))
        request.add_header("Authorization", "Bearer " + self.api_key)
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        try:
            with self.opener(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError("xAI Responses API returned {}: {}".format(
                error.code, _safe_error_detail(detail, self.api_key)))
        except urllib.error.URLError as error:
            raise RuntimeError("xAI Responses API unavailable: {}".format(error.reason))
        output_text = result.get("output_text")
        if not output_text:
            for item in result.get("output") or []:
                for content in item.get("content") or []:
                    if content.get("type") == "output_text" and content.get("text"):
                        output_text = content["text"]
                        break
                if output_text:
                    break
        if not output_text:
            raise RuntimeError("xAI response did not contain output text")
        try:
            return json.loads(output_text)
        except json.JSONDecodeError as error:
            raise RuntimeError("xAI response was not valid JSON: {}".format(error))


provider = XAIProvider()
