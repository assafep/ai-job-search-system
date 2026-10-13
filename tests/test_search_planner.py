from services.search_planner import derive_search_terms


def test_search_planner_uses_provider_and_limits_terms():
    class Provider:
        def generate_with_schema(self, prompt, context, schema):
            assert "source_resume" in context["applicant_profile"]
            return {"search_terms": [" backend ", "python"] * 8}

    terms = derive_search_terms(
        {"source_resume": {"path": "/tmp/resume.pdf"}}, Provider()
    )
    assert terms == ["backend", "python"] * 6


def test_search_planner_is_optional_for_replay_providers():
    assert derive_search_terms({}, lambda prompt, context: {}) == []
