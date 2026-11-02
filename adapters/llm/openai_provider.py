"""OpenAI-compatible chat adapter for stateless stage agents.

The adapter receives prompt text and serialized context. It does not receive
persistence clients or any other external-system handles. The provider name
and base URL come from adapters.llm.providers.
"""

import json
import os
import urllib.error
import urllib.request

from adapters.llm.providers import resolve_llm


def _safe_error_detail(detail, secret):
    """Keep provider errors useful without echoing credentials."""
    if secret:
        detail = detail.replace(secret, "[redacted]")
    return detail


def _message_text(result):
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _json_object(text):
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    try:
        value = json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError("Model response was not valid JSON: {}".format(error)) from error
    if not isinstance(value, dict):
        raise RuntimeError("Model response was not a JSON object")
    return value


class OpenAIProvider:
    """Callable-compatible provider backed by chat completions."""

    def __init__(self, api_key=None, model=None, endpoint=None, opener=None):
        self.settings = resolve_llm(os.environ, api_key=api_key, model=model, endpoint=endpoint)
        self.api_key = self.settings.api_key
        self.model = self.settings.model
        self.endpoint = self.settings.endpoint
        self.key_required = self.settings.key_required
        self.opener = opener or urllib.request.urlopen

    @property
    def configured(self):
        return self.settings.configured

    def generate(self, prompt, context):
        if not self.endpoint:
            raise RuntimeError(
                "Set AJSS_LLM_PROVIDER to grok, openai, anthropic, gemini, or an http(s) base URL"
            )
        if self.key_required and not self.api_key:
            raise RuntimeError("Set AJSS_LLM_API_KEY")
        if not self.model:
            raise RuntimeError("Set AJSS_LLM_MODEL to the model served at " + self.endpoint)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(context, dict):
            raise ValueError("context must be an object")

        input_text = "{}\n\nContext JSON:\n{}".format(
            prompt,
            json.dumps(context, ensure_ascii=False, sort_keys=True),
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": input_text}],
        }
        if self.settings.json_object:
            payload["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            self.endpoint,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
        )
        if self.api_key:
            request.add_header("Authorization", "Bearer " + self.api_key)
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        try:
            with self.opener(request) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError("Model API returned {}: {}".format(
                error.code, _safe_error_detail(detail, self.api_key))) from error
        except urllib.error.URLError as error:
            raise RuntimeError("Model API unavailable: {}".format(error.reason)) from error
        output_text = _message_text(result)
        if not output_text:
            raise RuntimeError("Model response did not contain message text")
        return _json_object(output_text)


provider = OpenAIProvider()
