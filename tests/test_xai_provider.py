import json

import pytest

from adapters.llm.xai_provider import XAIProvider


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


def test_xai_provider_sends_a_strict_schema_to_grok():
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse({"output_text": '{"search_terms":["backend"]}'})

    provider = XAIProvider(api_key="secret", model="grok-4.7", timeout=12, opener=opener)
    result = provider.generate_with_schema(
        "Plan the search.",
        {"applicant_profile": {"personal": {"name": "Ada"}}},
        {"type": "object", "properties": {"search_terms": {"type": "array", "items": {"type": "string"}}}},
    )

    assert result == {"search_terms": ["backend"]}
    request, timeout = requests[0]
    assert timeout == 12
    assert request.get_header("Authorization") == "Bearer secret"
    assert request.full_url == "https://api.x.ai/v1/responses"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["model"] == "grok-4.7"
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"]["additionalProperties"] is False
    assert "Ada" in payload["input"]


def test_xai_provider_can_browse_while_reading_a_posting():
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return FakeResponse({"output_text": '{"candidates":[]}'})

    provider = XAIProvider(api_key="secret", opener=opener)
    browsing = provider.with_tools([{"type": "web_search"}])
    browsing.generate_with_schema("Read the posting.", {"read_posting_url": "https://example.test/job"},
                                  {"type": "object", "properties": {"candidates": {"type": "array"}}})

    payload = json.loads(requests[0].data.decode("utf-8"))
    assert payload["tools"] == [{"type": "web_search"}]
    assert provider.tools is None


def test_xai_provider_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    provider = XAIProvider(api_key="")
    with pytest.raises(RuntimeError, match="XAI_API_KEY"):
        provider.generate_with_schema("Plan.", {}, {"type": "object", "properties": {}})


def test_xai_provider_redacts_the_key_from_http_errors():
    import urllib.error

    def opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 401, "denied", hdrs=None, fp=_Body(b'{"error":"bad key secret"}'))

    provider = XAIProvider(api_key="secret", opener=opener)
    with pytest.raises(RuntimeError, match="\\[redacted\\]") as caught:
        provider.generate("Plan.", {})
    assert "secret" not in str(caught.value)


class _Body:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload
