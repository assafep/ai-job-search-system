"""Small OpenAI Responses API adapter for stateless workflow agents.

The adapter deliberately receives only prompt text and serialized context. It
does not receive persistence clients or any other external-system handles.
"""

import json
import os
import urllib.error
import urllib.request


def _safe_error_detail(detail, secret):
    """Keep provider errors useful without echoing credentials."""
    if secret:
        detail = detail.replace(secret, "[redacted]")
    return detail


class OpenAIProvider:
    """Callable-compatible provider backed by the OpenAI Responses API."""

    def __init__(self, api_key=None, model=None, endpoint=None, opener=None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.endpoint = endpoint or os.environ.get(
            "OPENAI_RESPONSES_URL", "https://api.openai.com/v1/responses"
        )
        self.opener = opener or urllib.request.urlopen

    def generate(self, prompt, context):
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
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
            "input": input_text,
            "text": {"format": {"type": "json_object"}},
        }
        request = urllib.request.Request(
            self.endpoint,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
        )
        request.add_header("Authorization", "Bearer " + self.api_key)
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        try:
            with self.opener(request) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError("OpenAI Responses API returned {}: {}".format(
                error.code, _safe_error_detail(detail, self.api_key)))
        except urllib.error.URLError as error:
            raise RuntimeError("OpenAI Responses API unavailable: {}".format(error.reason))

        output_text = result.get("output_text")
        if not output_text:
            for item in result.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") == "output_text" and content.get("text"):
                        output_text = content["text"]
                        break
                if output_text:
                    break
        if not output_text:
            raise RuntimeError("OpenAI response did not contain output text")
        try:
            return json.loads(output_text)
        except json.JSONDecodeError as error:
            raise RuntimeError("OpenAI response was not valid JSON: {}".format(error))


provider = OpenAIProvider()
