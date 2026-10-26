"""Single-user background orchestrator for discovery and preparation."""

import argparse
import json
import sys
import time

from adapters.llm.codex_provider import provider
from .polling import PollingSchedule, enqueue_scheduled, enqueue_queues, maintenance_names, stage_poll_seconds
from .application_service_client import ApplicationServiceClient
from .runtime_config import load
from .workload_registry import registry_from_environment
from .stage_executor import runtime_from_environment
from .workload_worker import WorkloadWorker


class AutomationDaemon:
    def __init__(self, registry, runtime, persistence_client, agent_provider=provider):
        self.registry = registry
        self.runtime = runtime
        self.persistence_client = persistence_client
        self.worker = WorkloadWorker(runtime, agent_provider)
        self.schedule = PollingSchedule()

    def enqueue_discovery(self, source_names=None, results_per_page=20):
        return enqueue_scheduled(self.registry, names=['discover'], payloads={
            'discover': {'source_names': source_names or ['freehire'],
                         'results_per_page': results_per_page}})[0]

    def enqueue_bookmark_preparation(self):
        return enqueue_queues(self.persistence_client, self.registry,
                              names=['bookmark-discovered', 'validate-bookmarked', 'prepare-validated'])

    def run_cycle(self):
        from .external_event_polling import poll
        external_events = {}
        for name in ('handle-incoming-email', 'prepare-interview'):
            external_events.update(self.schedule.run('external-' + name, stage_poll_seconds(name),
                lambda name=name: poll(self.persistence_client, self.registry, stages=[name])) or {})
        enqueue_scheduled(self.registry, names=maintenance_names())
        self.enqueue_discovery()
        discovery_results = self.worker.run_once()
        package_workloads, preparation_results = [], []
        while True:
            batch = self.enqueue_bookmark_preparation()
            pending = self.registry.pending()
            if not pending:
                break
            package_workloads.extend(batch)
            results = self.worker.run_once()
            preparation_results.extend(results)
            if not results or any(result.get('status') == 'failed' for result in results):
                break
        submission_workloads = self.enqueue_ready_submission()
        submission_results = self.worker.run_once()
        return {
            'external_event_polling': external_events,
            "discovery_results": discovery_results,
            "package_workloads": [item['workload_id'] for item in package_workloads],
            "preparation_results": preparation_results,
            "submission_workloads": [item['workload_id'] for item in submission_workloads],
            "submission_results": submission_results,
            "status": self.registry.report(),
        }

    def enqueue_ready_submission(self):
        return enqueue_queues(self.persistence_client, self.registry, names=['submit-prepared'])

    def run_forever(self, interval_seconds=60):
        # Continuous operation has one implementation and one shared pool.
        from .orchestrator import run_pipelines
        run_pipelines()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run and monitor job-search automation")
    parser.add_argument("command", choices=["run-once", "status", "watch", "daemon"])
    parser.add_argument("--interval", type=float, default=60)
    args = parser.parse_args(argv)
    load(require=True)
    registry = registry_from_environment()
    persistence = ApplicationServiceClient()
    runtime = runtime_from_environment(persistence_client=persistence)
    runtime.registry = registry
    daemon = AutomationDaemon(registry, runtime, persistence)
    if args.command == "status":
        print(json.dumps(registry.report(), indent=2, ensure_ascii=False))
        return 0
    if args.command == "run-once":
        print(json.dumps(daemon.run_cycle(), indent=2, ensure_ascii=False, default=str))
        return 0
    if args.command == "watch":
        while True:
            print(json.dumps(registry.report(), ensure_ascii=False), flush=True)
            time.sleep(args.interval)
    daemon.run_forever(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
