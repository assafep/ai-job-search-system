"""The model client follows AJSS_LLM_PROVIDER and speaks chat completions."""
import json
import urllib.error
from io import BytesIO

import pytest


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


def test_named_provider_posts_chat_completions(monkeypatch):
    from adapters.llm.openai_provider import OpenAIProvider
    for name in ("OPENAI_API_KEY", "XAI_API_KEY", "AJSS_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AJSS_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("AJSS_LLM_API_KEY", "gemini-test")
    seen = {}

    def opener(request):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode())
        seen["auth"] = request.get_header("Authorization")
        return Response(b'{"choices":[{"message":{"content":"{\\"ok\\": true}"}}]}')

    provider = OpenAIProvider(opener=opener)
    assert provider.generate("Return JSON.", {"company": "Example"}) == {"ok": True}
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert seen["body"]["model"] == "gemini-3.8-flash"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["auth"] == "Bearer gemini-test"
    assert "gemini-test" not in seen["body"]["messages"][0]["content"]


def test_anthropic_omits_json_object_mode(monkeypatch):
    from adapters.llm.openai_provider import OpenAIProvider
    monkeypatch.setenv("AJSS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("AJSS_LLM_API_KEY", "anthropic-test")
    seen = {}

    def opener(request):
        seen["body"] = json.loads(request.data.decode())
        seen["url"] = request.full_url
        return Response(b'{"choices":[{"message":{"content":"```json\\n{\\"ok\\": true}\\n```"}}]}')

    assert OpenAIProvider(opener=opener).generate("Return JSON.", {}) == {"ok": True}
    assert seen["url"] == "https://api.anthropic.com/v1/chat/completions"
    assert seen["body"]["model"] == "claude-sonnet-5"
    assert "response_format" not in seen["body"]


def test_local_server_omits_authorization_when_no_key_is_set(monkeypatch):
    from adapters.llm.openai_provider import OpenAIProvider
    for name in ("OPENAI_API_KEY", "XAI_API_KEY", "AJSS_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AJSS_LLM_PROVIDER", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("AJSS_LLM_MODEL", "local-demo")
    seen = {}

    def opener(request):
        seen["auth"] = request.get_header("Authorization")
        seen["url"] = request.full_url
        return Response(b'{"choices":[{"message":{"content":"{\\"ok\\": true}"}}]}')

    assert OpenAIProvider(opener=opener).generate("Return JSON.", {}) == {"ok": True}
    assert seen["auth"] is None
    assert seen["url"] == "http://127.0.0.1:11434/v1/chat/completions"


def test_provider_error_redacts_the_key(monkeypatch):
    from adapters.llm.openai_provider import OpenAIProvider
    monkeypatch.setenv("AJSS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AJSS_LLM_API_KEY", "sk-planted")

    def opener(request):
        raise urllib.error.HTTPError(
            request.full_url, 401, "no", None, BytesIO(b"rejected sk-planted"),
        )

    with pytest.raises(RuntimeError, match="401") as raised:
        OpenAIProvider(opener=opener).generate("Return JSON.", {})
    assert "sk-planted" not in str(raised.value)
    assert "[redacted]" in str(raised.value)


def test_missing_provider_names_the_generic_variables(monkeypatch):
    from adapters.llm.openai_provider import OpenAIProvider
    for name in ("OPENAI_API_KEY", "XAI_API_KEY", "AJSS_LLM_API_KEY", "AJSS_LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="AJSS_LLM_PROVIDER"):
        OpenAIProvider().generate("Return JSON.", {})
