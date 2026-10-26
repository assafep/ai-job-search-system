"""Process entrypoint for draining queued workloads with an injected provider."""

import argparse
import importlib
import json
import os
import signal
from pathlib import Path
from threading import Event
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


def load_discovery_provider(spec=None, runtime_config=None):
    """Load the scout provider. A module override wins; otherwise use ``CODING_AGENT``."""
    spec = spec or os.environ.get("DISCOVERY_PROVIDER")
    if spec:
        return load_provider(spec)
    from adapters.llm.coding_agent import CodingAgentProvider
    from .runtime_config import load
    runtime_config = load(require=False) if runtime_config is None else runtime_config
    scout = runtime_config.get('scout') or {}
    return CodingAgentProvider(
        executable=os.environ.get('CODING_AGENT') or scout.get('coding_agent'),
        model=os.environ.get('CODING_AGENT_MODEL') or scout.get('model'))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Drain queued workflow workloads")
    parser.add_argument("--provider", default=None, help="LLM provider as module:attribute")
    parser.add_argument("--once", action="store_true", help="drain current pending workloads and exit")
    parser.add_argument("--limit", type=int, default=None, help="maximum workloads for a one-shot drain")
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--poll-workflows", action="store_true", help="Poll scheduled and state-queue workflows; external events arrive separately")
    args = parser.parse_args(argv)
    try:
        if args.limit is not None and not args.once:
            raise ValueError("--limit requires --once")
        from .orchestration_lock import runner_lock
        stop_event = Event()
        previous_handlers = {}
        ready_path = Path(os.environ['AJSS_WORKER_READY_PATH']) if os.environ.get('AJSS_WORKER_READY_PATH') else None
        # Install handlers before initialization so startup can also be stopped.
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, lambda *_: stop_event.set())
        try:
            with runner_lock():
                if ready_path:
                    ready_path.unlink(missing_ok=True)
                provider = load_provider(args.provider)
                runtime = runtime_from_environment()
                if any(record.status == 'running' for record in runtime.registry.snapshot()):
                    raise RuntimeError('Interrupted workloads require manual reconciliation before restarting the runner')
                worker = WorkloadWorker(runtime, provider)
                if ready_path and not stop_event.is_set():
                    from .worker_health import mark_ready
                    mark_ready(ready_path)
                try:
                    results = worker.run_once(args.limit, stop_event=stop_event) if args.once else worker.run_forever(args.poll_seconds, stop_event=stop_event, poll_workflows=args.poll_workflows)
                finally:
                    if ready_path:
                        ready_path.unlink(missing_ok=True)
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
        if args.once:
            print(json.dumps(results, indent=2, ensure_ascii=False, default=lambda value: value.__dict__))
        return 0
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
