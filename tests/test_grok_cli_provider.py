from adapters.llm.coding_agent import CodingAgentProvider
from adapters.llm.grok_cli_provider import provider


def test_grok_wrapper_is_a_coding_agent_fixed_to_the_grok_command():
    assert isinstance(provider, CodingAgentProvider)
    assert provider.executable == "grok"
    assert provider.model is None
