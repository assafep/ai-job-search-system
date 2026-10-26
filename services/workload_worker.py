"""Continuous worker boundary for queued workflow workloads."""

import time


class WorkloadWorker:
    """Drain a registry through ``WorkflowRuntime`` with an injected provider."""

    def __init__(self, runtime, agent_provider):
        if not callable(agent_provider) and not callable(getattr(agent_provider, "generate", None)):
            raise TypeError("agent_provider must be callable or expose generate(prompt, context)")
        self.runtime = runtime
        self.agent_provider = agent_provider

    def run_once(self, limit=None, stop_event=None):
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
            raise ValueError("limit must be a positive integer")
        records = [record for record in self.runtime.registry.pending()
                   if record.workflow not in {'validate-bookmarked', 'submit-prepared'}]
        if limit is not None:
            records = records[:limit]
        results = []
        for record in records:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                results.append(self.runtime.run(record.workload_id, agent_provider=self.agent_provider))
            except Exception as exc:
                results.append({
                    "workload_id": record.workload_id,
                    "status": "failed",
                    "error": str(exc),
                })
        return results

    def run_forever(self, poll_seconds=60, stop_event=None, poll_workflows=False):
        """Poll until ``stop_event`` is set; suitable for a scheduler process."""
        if not isinstance(poll_seconds, (int, float)) or isinstance(poll_seconds, bool) or poll_seconds < 0:
            raise ValueError("poll_seconds must be a non-negative number")
        from .polling import PollingSchedule, definitions, enqueue_queues, enqueue_scheduled, workflow_poll_seconds
        schedule = PollingSchedule()
        while stop_event is None or not stop_event.is_set():
            if poll_workflows:
                for name, definition in definitions().items():
                    if stop_event is not None and stop_event.is_set():
                        break
                    if name in {'validate-bookmarked', 'submit-prepared'}:
                        continue
                    kind = definition['trigger']['poll_kind']
                    if kind == 'queue':
                        callback = lambda name=name: enqueue_queues(self.runtime.persistence_client, self.runtime.registry, names=[name])
                    elif kind == 'scheduled':
                        callback = lambda name=name: enqueue_scheduled(self.runtime.registry, names=[name])
                    else:
                        continue  # Gmail/Calendar event adapters admit external events separately.
                    schedule.run(name, workflow_poll_seconds(name), callback)
            self.run_once(stop_event=stop_event)
            if stop_event is None:
                time.sleep(poll_seconds)
            else:
                stop_event.wait(poll_seconds)
