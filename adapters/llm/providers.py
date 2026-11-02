"""Named OpenAI-compatible chat providers.

AJSS_LLM_PROVIDER is a catalog name or an http(s) base URL. AJSS_LLM_API_KEY is
the key for every named provider. A base URL may omit the key. Older
OPENAI_API_KEY and XAI_API_KEY values still select OpenAI or Grok when the
generic provider name is unset.
"""

from urllib.parse import urlsplit


# Model ids and base URLs are the ones published by each vendor.
# Grok: docs.x.ai quickstart, model grok-4.7, base https://api.x.ai/v1.
# Gemini: ai.google.dev OpenAI compatibility, model gemini-3.8-flash.
# Anthropic: platform.claude.com OpenAI SDK compatibility, model claude-sonnet-5.
# The compatibility layer rejects response_format json_object, so that provider
# relies on the agent prompt to request JSON.
PROVIDERS = {
    "grok": {
        "label": "Grok",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.7",
        "key_url": "https://console.x.ai/team/default/api-keys",
        "key_label": "Create a Grok API key",
        "legacy_key": "XAI_API_KEY",
        "legacy_model": "XAI_MODEL",
        "json_object": True,
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_url": "https://platform.openai.com/api-keys",
        "key_label": "Create an OpenAI API key",
        "legacy_key": "OPENAI_API_KEY",
        "legacy_model": "OPENAI_MODEL",
        "json_object": True,
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-5",
        "key_url": "https://platform.claude.com/settings/keys",
        "key_label": "Create an Anthropic API key",
        "legacy_key": "",
        "legacy_model": "",
        "json_object": False,
    },
    "gemini": {
        "label": "Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.8-flash",
        "key_url": "https://aistudio.google.com/apikey",
        "key_label": "Create a Gemini API key",
        "legacy_key": "",
        "legacy_model": "",
        "json_object": True,
    },
}

LOCAL_SENTINEL = "local"
PROVIDER_CHOICES = tuple(PROVIDERS) + (LOCAL_SENTINEL,)


class LlmSelection:
    """Resolved provider, without constructor overrides."""

    def __init__(self, phase, name, label, base_url, model, api_key, key_required,
                 json_object, key_url, key_label):
        self.phase = phase
        self.name = name
        self.label = label
        self.base_url = base_url
        self.model = model
        self.api_key = api_key or None
        self.key_required = key_required
        self.json_object = json_object
        self.key_url = key_url
        self.key_label = key_label

    @property
    def provider_ready(self):
        return self.phase in {"named", "url"}

    @property
    def model_ready(self):
        if self.phase == "named":
            return True
        if self.phase == "url":
            return bool(self.model)
        return False

    def key_ready(self, declined=False):
        if self.phase == "named":
            return bool(self.api_key)
        if self.phase == "url":
            return bool(self.api_key) or bool(declined)
        return False


class LlmSettings:
    """Call settings after optional constructor overrides."""

    def __init__(self, selection, model, api_key, base_url, key_required):
        self.selection = selection
        self.name = selection.name
        self.model = model
        self.api_key = api_key or None
        self.base_url = base_url
        self.key_required = key_required
        self.json_object = selection.json_object
        self.endpoint = chat_completions_url(base_url)

    @property
    def configured(self):
        return bool(self.endpoint and self.model and (not self.key_required or self.api_key))


def chat_completions_url(base):
    text = (base or "").strip().rstrip("/")
    if not text:
        return ""
    if text.endswith("/chat/completions"):
        return text
    return text + "/chat/completions"


def is_base_url(value):
    text = (value or "").strip()
    parts = urlsplit(text)
    return (
        parts.scheme in {"http", "https"}
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
        and not parts.query
        and not parts.fragment
    )


def normalize_base_url(value):
    text = (value or "").strip().rstrip("/")
    if not is_base_url(text):
        raise ValueError(
            "llm_base_url must be an http or https base URL with no user, password, or query. "
            "Example: http://host.docker.internal:11434/v1"
        )
    return text


def parse_provider_choice(value):
    name = (value or "").strip().casefold()
    if name in {"local", "custom"}:
        return LOCAL_SENTINEL
    if name in PROVIDERS:
        return name
    raise ValueError("llm_provider must be grok, openai, anthropic, gemini, or local")


def _text(env, name):
    return (env.get(name) or "").strip()


def _named(name, env, api_key):
    spec = PROVIDERS[name]
    key = api_key
    if not key and spec["legacy_key"]:
        key = _text(env, spec["legacy_key"])
    model = _text(env, "AJSS_LLM_MODEL")
    if not model and spec["legacy_model"]:
        model = _text(env, spec["legacy_model"])
    if not model:
        model = spec["model"]
    return LlmSelection(
        "named", name, spec["label"], spec["base_url"], model, key, True,
        spec["json_object"], spec["key_url"], spec["key_label"],
    )


def selection(env):
    """Read AJSS_LLM_PROVIDER, then the older OpenAI and Grok key names."""
    raw = _text(env, "AJSS_LLM_PROVIDER")
    folded = raw.casefold()
    generic_key = _text(env, "AJSS_LLM_API_KEY")
    if folded in PROVIDERS:
        return _named(folded, env, generic_key)
    if raw and is_base_url(raw):
        return LlmSelection(
            "url", "local", "Custom", raw.rstrip("/"), _text(env, "AJSS_LLM_MODEL"),
            generic_key, False, True, "", "",
        )
    if folded == LOCAL_SENTINEL:
        return LlmSelection("need_url", "", "", "", "", None, True, True, "", "")
    if raw:
        return LlmSelection("invalid", "", "", "", "", None, True, True, "", "")
    if _text(env, "OPENAI_API_KEY"):
        return _named("openai", env, generic_key or _text(env, "OPENAI_API_KEY"))
    if _text(env, "XAI_API_KEY"):
        return _named("grok", env, generic_key or _text(env, "XAI_API_KEY"))
    return LlmSelection("choose", "", "", "", "", None, True, True, "", "")


def resolve_llm(env, api_key=None, model=None, endpoint=None):
    """Apply explicit constructor arguments on top of the environment."""
    selected = selection(env)
    key = (api_key or "").strip() or selected.api_key
    chosen_model = (model or "").strip() or selected.model
    base = selected.base_url
    key_required = selected.key_required
    if endpoint:
        supplied = endpoint.strip()
        suffix = "/chat/completions"
        base = supplied[:-len(suffix)].rstrip("/") if supplied.endswith(suffix) else supplied.rstrip("/")
        if not selected.provider_ready:
            key_required = bool(key)
            if not chosen_model:
                chosen_model = PROVIDERS["openai"]["model"]
    return LlmSettings(selected, chosen_model, key, base, key_required)
