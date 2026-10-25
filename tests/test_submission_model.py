from adapters.browser.comeet import form_answers


class RecordingProvider:
    def __init__(self):
        self.calls = []

    def generate_with_schema(self, prompt, context, schema):
        self.calls.append((prompt, context, schema))
        return {"answers": [{"label": "Personal note", "value": "Backend engineer."}]}


def test_submission_asks_grok_for_extra_answers():
    provider = RecordingProvider()
    answers = form_answers({
        "reasoning_effort": "medium",
        "application": {"company": "Example", "role": "Engineer", "posting_url": "https://example.test/job"},
        "applicant_profile": {"personal": {"name": "Ada"}},
    }, provider)
    assert answers == [{"label": "Personal note", "value": "Backend engineer."}]
    assert provider.calls[0][1]["company"] == "Example"
    assert "package_bytes" not in provider.calls[0][1]
