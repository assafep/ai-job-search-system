"""Provider contract for LLM adapters."""


class LLMProvider:
    """Minimal adapter shape consumed by ``services.agent_runner``.

    Concrete providers may wrap any SaaS SDK, but must return a decoded JSON
    object or JSON string and must not perform workflow persistence.
    """

    def generate(self, prompt, context):
        raise NotImplementedError
