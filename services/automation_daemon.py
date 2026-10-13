"""Single-user background orchestrator for discovery and preparation."""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from adapters.llm.codex_provider import provider
from .event_dispatcher import dispatch
from .persistence_client import PersistenceApiClient
from .runtime_config import load
from .workload_registry import registry_from_environment
from .workflow_runtime import runtime_from_environment
from .workload_worker import WorkloadWorker


class AutomationDaemon:
    def __init__(self, registry, runtime, persistence_client, agent_provider=provider):
        self.registry = registry
        self.runtime = runtime
        self.persistence_client = persistence_client
        self.worker = WorkloadWorker(runtime, agent_provider)

    def enqueue_discovery(self, source_names=None, results_per_page=20):
        event_id = "discovery-{}".format(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        return dispatch(
            "job_discovery_bookmark_schedule",
            {"source_names": source_names or ["freehire"], "results_per_page": results_per_page},
            event_id=event_id,
            registry=self.registry,
        )

    def enqueue_bookmark_preparation(self):
        applications = self.persistence_client.list_applications(status="Bookmarked") or []
        queued = {
            (record.event.get("payload") or {}).get("application_id")
            for record in self.registry.records.values()
            if record.workflow == "prepare-bookmarked" and record.status in {"queued", "running"}
        }
        created = []
        for application in applications:
            application_id = application.get("application_id")
            if not application_id or application_id in queued:
                continue
            events = self.persistence_client.list_events(application_id) or []
            if any(event.get("event_type") == "application_package_prepared" for event in events):
                continue
            artifacts = (
                self.persistence_client.list_artifacts(application_id)
                if hasattr(self.persistence_client, "list_artifacts") else []
            ) or []
            created.append(dispatch(
                "application_package_schedule",
                {
                    "application_id": application_id,
                    "existing_application_package": any(
                        str(item.get("name", "")).lower().endswith(".pdf")
                        or str(item.get("name", "")).lower() in {
                            "application package", "application-package.pdf", "application pdf"
                        }
                        for item in artifacts
                    ),
                    "existing_application_package_reference": next((
                        "https://drive.google.com/file/d/{}/view".format(item["drive_file_id"])
                        for item in artifacts if item.get("drive_file_id")
                    ), None),
                },
                event_id="package-{}".format(application_id),
                registry=self.registry,
            ))
        return created

    def run_cycle(self):
        from .external_event_polling import poll
        external_events = poll(self.persistence_client, self.registry)
        self.enqueue_discovery()
        discovery_results = self.worker.run_once()
        package_workloads = self.enqueue_bookmark_preparation()
        preparation_results = self.worker.run_once()
        submission_workloads = self.enqueue_ready_submission()
        submission_results = self.worker.run_once()
        return {
            'external_event_polling': external_events,
            "discovery_results": discovery_results,
            "package_workloads": [item.workload_id for item in package_workloads],
            "preparation_results": preparation_results,
            "submission_workloads": [item['workload_id'] for item in submission_workloads],
            "submission_results": submission_results,
            "status": self.registry.report(),
        }

    def enqueue_ready_submission(self):
        return [dispatch('application_submission_schedule',
                         {'application_id': application['application_id']},
                         event_id='submit-' + application['application_id'], registry=self.registry)
                for application in self.persistence_client.list_applications(status='Ready for Auto-Apply') or []]

    def run_forever(self, interval_seconds=86400):
        while True:
            self.run_cycle()
            time.sleep(interval_seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run and monitor job-search automation")
    parser.add_argument("command", choices=["run-once", "status", "watch", "daemon"])
    parser.add_argument("--interval", type=float, default=86400)
    args = parser.parse_args(argv)
    load(require=True)
    registry = registry_from_environment()
    persistence = PersistenceApiClient()
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
