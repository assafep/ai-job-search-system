"""Process entrypoint for draining queued workloads with an injected provider."""

import argparse
import importlib
import json
import os
import sys

from .workload_worker import WorkloadWorker
from .workflow_runtime import runtime_from_environment


def load_provider(spec=None):
    """Load ``module:attribute`` from deployment configuration."""
    spec = spec or os.environ.get(
        "AGENT_PROVIDER", "adapters.llm.codex_provider:provider"
    )
    if ":" not in spec:
        raise ValueError("AGENT_PROVIDER must be a module:attribute provider path")
    module_name, attribute = spec.split(":", 1)
    if not module_name or not attribute:
        raise ValueError("AGENT_PROVIDER must be a module:attribute provider path")
    provider = getattr(importlib.import_module(module_name), attribute)
    if not callable(provider) and not callable(getattr(provider, "generate", None)):
        raise TypeError("configured agent provider must be callable or expose generate(prompt, context)")
    return provider


def main(argv=None):
    parser = argparse.ArgumentParser(description="Drain queued workflow workloads")
    parser.add_argument("--provider", default=None, help="LLM provider as module:attribute")
    parser.add_argument("--once", action="store_true", help="drain current pending workloads and exit")
    parser.add_argument("--limit", type=int, default=None, help="maximum workloads for a one-shot drain")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        if args.limit is not None and not args.once:
            raise ValueError("--limit requires --once")
        provider = load_provider(args.provider)
        worker = WorkloadWorker(runtime_from_environment(), provider)
        results = worker.run_once(args.limit) if args.once else worker.run_forever(args.poll_seconds)
        if args.once:
            print(json.dumps(results, indent=2, ensure_ascii=False, default=lambda value: value.__dict__))
        return 0
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
