"""Continuous worker boundary for queued workflow workloads."""

import time


class WorkloadWorker:
    """Drain a registry through ``WorkflowRuntime`` with an injected provider."""

    def __init__(self, runtime, agent_provider):
        if not callable(agent_provider) and not callable(getattr(agent_provider, "generate", None)):
            raise TypeError("agent_provider must be callable or expose generate(prompt, context)")
        self.runtime = runtime
        self.agent_provider = agent_provider

    def run_once(self, limit=None):
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
            raise ValueError("limit must be a positive integer")
        records = self.runtime.registry.pending()
        if limit is not None:
            records = records[:limit]
        results = []
        for record in records:
            try:
                results.append(self.runtime.run(record.workload_id, agent_provider=self.agent_provider))
            except Exception as exc:
                results.append({
                    "workload_id": record.workload_id,
                    "status": "failed",
                    "error": str(exc),
                })
        return results

    def run_forever(self, poll_seconds=60, stop_event=None):
        """Poll until ``stop_event`` is set; suitable for a scheduler process."""
        if not isinstance(poll_seconds, (int, float)) or isinstance(poll_seconds, bool) or poll_seconds < 0:
            raise ValueError("poll_seconds must be a non-negative number")
        while stop_event is None or not stop_event.is_set():
            self.run_once()
            if stop_event is None:
                time.sleep(poll_seconds)
            else:
                stop_event.wait(poll_seconds)
