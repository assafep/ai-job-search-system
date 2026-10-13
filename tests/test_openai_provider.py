import json

from adapters.llm.openai_provider import OpenAIProvider


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


def test_openai_provider_returns_decoded_json_and_sends_context():
    requests = []

    def opener(request):
        requests.append(request)
        return FakeResponse({"output_text": '{"decision":"review"}'})

    provider = OpenAIProvider(api_key="secret", model="test-model", opener=opener)
    result = provider.generate("Decide.", {"role": "Engineer"})

    assert result == {"decision": "review"}
    assert requests[0].get_header("Authorization") == "Bearer secret"
    payload = json.loads(requests[0].data.decode("utf-8"))
    assert payload["model"] == "test-model"
    assert payload["text"]["format"]["type"] == "json_object"
    assert "Engineer" in payload["input"]
