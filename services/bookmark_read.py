"""Read one discovered posting: exact URL first, language model if that fails."""

import os

from adapters.job_boards.framework import fetch_posting_details
from adapters.llm.coding_agent import CodingAgentProvider
from .agent_runner import run as run_agent
from .runtime_config import load
from .scout_reading import PostingReadFailure, posting_from_model


def configured_reader(provider=None):
    """Read this stage's own model and effort from config/runtime.json."""
    if provider is not None:
        if not callable(provider) and not (hasattr(provider, 'generate') or hasattr(provider, 'generate_with_schema')):
            raise ValueError('Hosted bookmark reader needs an API-backed provider')
        return provider, getattr(provider, 'model', 'configured API provider'), 'medium'
    section = (load(require=True).get("bookmark-discovered") or {})
    agent = str(section.get("coding_agent") or "").strip()
    model = str(section.get("model") or "").strip()
    effort = str(section.get("effort") or "").strip()
    if not agent or not model or effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError(
            "config/runtime.json bookmark-discovered needs coding_agent, model, and effort. "
            "Rerun onboarding.")
    reader = provider if hasattr(provider, "generate_with_schema") else CodingAgentProvider(
        executable=agent, model=model)
    if hasattr(reader, "with_reasoning_effort"):
        reader = reader.with_reasoning_effort(effort)
    if hasattr(reader, "model"):
        reader.model = model
    return reader, model, effort


def _output(company, role, posting_url, posting):
    return {
        "company": company,
        "role": role,
        "posting_url": posting_url,
        "resolved_url": posting.get("source_url") or posting_url,
        "description": posting["description"],
        "read_failure": "",
    }


def read_discovered_posting(posting_url, company, role, provider=None):
    """Return (agent output, attempt or None). Raise when both reads fail.

    The exact URL is fetched again on every bookmark. The saved posting
    artifact is replaced with that result; a previously stored copy is not read.
    """
    try:
        posting = fetch_posting_details(posting_url)
    except Exception as url_failure:
        reader, model, effort = configured_reader(provider)
        logic = [
            "Fetched the exact posting URL.",
            "The URL read failed. Trying again with the language model.",
        ]
        obstacles = [str(url_failure)]
        try:
            output = run_agent("read-posting", {
                "read_posting_url": posting_url,
                "posting_url": posting_url,
                "company": company,
                "role": role,
            }, reader)
            posting = posting_from_model(posting_url, company, role, output)
        except Exception as model_failure:
            raise PostingReadFailure(
                "url reader: {}; model reader: {}".format(url_failure, model_failure),
                {
                    "model": model,
                    "effort": effort,
                    "logic": logic,
                    "obstacles": obstacles + [str(model_failure)],
                    "tackled": [],
                },
            ) from model_failure
        if posting is None:
            raise PostingReadFailure("model reader returned no posting", {
                "model": model,
                "effort": effort,
                "logic": logic,
                "obstacles": obstacles + ["The model returned no posting."],
                "tackled": [],
            })
        return _output(company, role, posting_url, posting), {
            "model": model,
            "effort": effort,
            "logic": logic,
            "obstacles": obstacles,
            "tackled": ["The language model read the posting."],
        }
    return _output(company, role, posting_url, posting), None
