"""Compatibility wrapper. Prefer ``CODING_AGENT`` and ``adapters.llm.coding_agent``."""

from adapters.llm.coding_agent import CodingAgentProvider

provider = CodingAgentProvider(executable="grok")
