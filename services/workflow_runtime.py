"""Lifecycle boundary between queued workloads and stateless workflow execution."""

import argparse
import json
import sys
from dataclasses import asdict

from .agent_runner import run as run_agent
from .approval_service import evaluate
from .approval_registry import approval_registry_from_environment
from .runtime_config import calendly_url as configured_calendly_url
from .workload_runner import WORKFLOW_AGENTS, execute
from .workload_registry import registry_from_environment
from persistence_api.profile import calendly_url as profile_calendly_url


class WorkflowRuntime:
    """Claim, execute, and settle one workload registry record.

    Agent results are supplied by the caller after the stateless agent has run.
    This class owns only workload bookkeeping; application mutations still go
    through the persistence API client passed to ``run``.
    """

    def __init__(self, registry, persistence_client, approval_registry=None, runtime_context=None):
        self.registry = registry
        self.persistence_client = persistence_client
        self.approval_registry = approval_registry
        self.runtime_context = runtime_context or {}

    def _record_preparation_failure(self, record, error):
        """Route preparation failures through the data layer, with retry-safe history."""
        if record.workflow != 'prepare-bookmarked':
            return
        application_id = (record.event.get('payload') or {}).get('application_id')
        if not application_id:
            return
        from .persistence_client import PersistenceApiError
        try:
            application = self.persistence_client.get_application(application_id)
        except KeyError:
            return  # A deleted posting must not be recreated by failure handling.
        except PersistenceApiError as exc:
            if 'returned 404:' in str(exc):
                return
            raise
        if application is None:
            return
        state = application.get('status') if isinstance(application, dict) else application.status
        if state not in {'Bookmarked', 'Ready for Auto-Apply', 'Manual Apply Required'}:
            return  # Never regress a submitted or closed application.
        self.persistence_client.transition(application_id, 'Manual Apply Required', {
            'source': 'application package preparation agent',
            'bullets': ['Application package preparation failed: ' + str(error)],
        }, 'preparation-failed-' + record.workload_id)

    def _fail_workload(self, record, error):
        self.registry.fail(record.workload_id, str(error))
        try:
            self._record_preparation_failure(record, error)
        except Exception as routing_error:
            raise RuntimeError('Preparation failed: {}. Recording Manual Apply Required also failed: {}'.format(
                error, routing_error)) from routing_error

    def _enrich_agent_context(self, workflow, context, agent_provider=None, application_snapshot=None):
        """Read workflow context through the persistence API, never into agents."""
        profile = None
        if hasattr(self.persistence_client, "get_profile"):
            profile = self.persistence_client.get_profile()
            context["applicant_profile"] = profile
            if not context.get("calendly_url"):
                context["calendly_url"] = profile_calendly_url(profile)
        if workflow in ("discover-and-apply", "discover-and-bookmark"):
            self._enrich_discovery_context(context, profile, agent_provider)
        if workflow in ("prepare-bookmarked", "discover-and-apply"):
            context["source_resume_path"] = (profile or {}).get("source_resume", {}).get("path")
            if not context["source_resume_path"]:
                raise ValueError("original onboarding resume PDF is required to prepare an application package")
            context.pop("cv_html", None)
        application_id = context.get("application_id")
        if not application_id:
            return context
        if application_snapshot is not None:
            context['application'] = application_snapshot
        elif hasattr(self.persistence_client, "get_application"):
            context["application"] = self.persistence_client.get_application(application_id)
        if workflow == "prepare-bookmarked":
            from adapters.job_boards.posting_details import fetch_posting_details
            from pypdf import PdfReader
            application = context.get("application") or {}
            context["job_posting"] = fetch_posting_details(application["posting_url"])
            context["resume_text"] = '\n'.join(
                page.extract_text() or '' for page in PdfReader(context["source_resume_path"]).pages)
        if hasattr(self.persistence_client, "list_events"):
            context["application_events"] = self.persistence_client.list_events(application_id)
        if workflow == "prepare-interview" and hasattr(self.persistence_client, "list_research"):
            context["existing_research"] = self.persistence_client.list_research(application_id)
        return context

    @staticmethod
    def _enrich_discovery_context(context, profile=None, agent_provider=None):
        """Fetch source postings before invoking the discovery agent.

        The source adapter is deliberately optional for replay and tests. A
        scheduled event explicitly naming Adzuna must have credentials and a
        query, otherwise failing here is safer than producing fabricated jobs.
        """
        source_names = context.get("source_names") or []
        source = next((name for name in ("freehire", "jobicy", "adzuna") if name in source_names), None)
        if source is None:
            return

        job_search = profile.get("job_search", {}) if isinstance(profile, dict) else {}
        terms = context.get("search_terms") or job_search.get("search_terms") or []
        if not terms and agent_provider is not None:
            from .search_planner import derive_search_terms
            terms = derive_search_terms(profile, agent_provider)
            if terms:
                context["search_terms"] = terms
        locations = context.get("locations") or []
        if isinstance(terms, str):
            terms = [terms]
        if isinstance(locations, str):
            locations = [locations]
        query = " OR ".join(str(item).strip() for item in terms if str(item).strip())
        where = " OR ".join(str(item).strip() for item in locations if str(item).strip())
        if source == "jobicy":
            from adapters.job_boards.jobicy import JobicyJobBoard
            context["job_postings"] = JobicyJobBoard().search(
                query=query,
                count=int(context.get("results_per_page", 100)),
            )
            return
        if source == "freehire":
            from adapters.job_boards.freehire import FreehireJobBoard
            countries = context.get("countries") or job_search.get("countries") or [
                item.upper() for item in locations if isinstance(item, str) and len(item) == 2
            ] or ["IL"]
            context["job_postings"] = FreehireJobBoard().search(
                query=query,
                countries=countries,
                limit=int(context.get("results_per_page", 100)),
            )
            return
        from adapters.job_boards.adzuna import client_from_environment
        if not query and not where:
            raise ValueError("Adzuna discovery requires search_terms or locations")
        context["job_postings"] = client_from_environment().search(
            what=query, where=where, page=int(context.get("page", 1)),
            results_per_page=int(context.get("results_per_page", 20)),
        )

    def run(self, workload_id, agent_results=None, agent_provider=None):
        # A reconciliation tick is the recovery boundary for interrupted
        # workloads. Reset recoverable running records before claiming the
        # reconciliation workload itself; application mutations remain under
        # the persistence API.
        record = self.registry.get(workload_id)
        previous_error = record.error
        if record.workflow == 'prepare-bookmarked' and agent_results is None and hasattr(self.persistence_client, 'list_artifacts'):
            from .application_package import saved_package, promote_saved_package
            self.registry.claim(workload_id)
            try:
                app = self.persistence_client.get_application((record.event.get('payload') or {})['application_id'])
                if app is not None and not isinstance(app, dict):
                    app = asdict(app)
                if app['status'] != 'Bookmarked':
                    if app['status'] == 'Manual Apply Required' and previous_error:
                        self._record_preparation_failure(record, previous_error)
                    self.registry.complete(workload_id)
                    return {'workflow': record.workflow, 'workload_id': workload_id, 'status': 'succeeded',
                            'application_id': app['application_id'],
                            'activity': 'Application is already {}; no package regeneration is needed.'.format(app['status'])}
                artifact = saved_package(self.persistence_client, app)
                if artifact:
                    result = promote_saved_package(self.persistence_client, app, artifact)
                    self.registry.complete(workload_id)
                    return {'workflow': record.workflow, 'workload_id': workload_id, 'status': 'succeeded',
                            'application_id': app['application_id'], 'activity': 'Reused saved package and promoted automatically.',
                            'package_reference': artifact['reference'], 'application': result}
                # Continue the normal generation path without a second claim.
                preclaimed = True
            except Exception as exc:
                self._fail_workload(record, exc)
                raise
        else:
            preclaimed = False
        if record.workflow == 'submit-ready':
            from .ready_submission import ReadySubmission
            self.registry.claim(workload_id)
            runner = ReadySubmission(self.persistence_client, self.registry)
            try:
                application_id = (record.event.get('payload') or {})['application_id']
                outcome = runner.execute(application_id, workload_id)
                if outcome['status'] in {'Submitted successfully', 'Already submitted', 'Blocked'}:
                    self.registry.complete(workload_id)
                else:
                    self.registry.wait_for_input(workload_id, '; '.join(outcome.get('blockers') or [outcome['status']]))
                return {'workload_id': workload_id, 'workflow': 'submit-ready',
                        'status': self.registry.get(workload_id).status, 'submission': outcome}
            except Exception as exc:
                self.registry.fail(workload_id, str(exc))
                raise
        registry_report = None
        approval_report = None
        if record.workflow == "reconcile":
            self.registry.recover_incomplete()
            registry_report = self.registry.report()
            if self.approval_registry is not None:
                approval_report = self.approval_registry.report()
        record = self.registry.get(workload_id) if preclaimed else self.registry.claim(workload_id)
        event = record.event or {}
        context = dict(self.runtime_context)
        context.update(event.get("payload") or {})
        context["workload_id"] = record.workload_id
        context.setdefault("trigger", {
            "type": event.get("event_type"),
            "event_id": event.get("event_id"),
        })
        approval_outcomes = []
        try:
            if agent_results is None:
                if agent_provider is None:
                    raise ValueError("agent_results or agent_provider is required")
                if hasattr(agent_provider, "with_reasoning_effort"):
                    from .workload_effort import effort_for_workflow
                    context["reasoning_effort"] = effort_for_workflow(record.workflow)
                    agent_provider = agent_provider.with_reasoning_effort(context["reasoning_effort"])
                # Reuse only the posting read by this runtime's package check,
                # never an application object supplied in an event payload.
                context = self._enrich_agent_context(record.workflow, context, agent_provider,
                                                     application_snapshot=app if preclaimed else None)
                agent_results = {}
                for agent_name in WORKFLOW_AGENTS[record.workflow]:
                    agent_context = dict(context)
                    if agent_name == "application" and "job-discovery" in agent_results:
                        discovery = agent_results["job-discovery"]
                        if isinstance(discovery, dict):
                            agent_context["discovered_candidates"] = discovery.get("candidates", [])
                    agent_results[agent_name] = run_agent(agent_name, agent_context, agent_provider)
            requested_actions = context.get("approval_actions") or []
            if not isinstance(requested_actions, list):
                raise ValueError("approval_actions must be an array")
            if any(not isinstance(action, str) or not action.strip() for action in requested_actions):
                raise ValueError("approval_actions must contain non-empty strings")
            approval_outcomes = evaluate(requested_actions)
            if self.approval_registry is not None:
                for outcome in approval_outcomes:
                    if outcome["decision"] != "ask":
                        continue
                    request = self.approval_registry.request(
                        outcome["action"],
                        record.workload_id,
                        {"workflow": record.workflow, "context": context},
                        "{}:approval:{}".format(record.workload_id, outcome["action"]),
                    )
                    outcome["request"] = asdict(request)
            result = execute(
                record.workflow,
                context,
                agent_results,
                self.persistence_client,
            )
        except Exception as exc:
            self._fail_workload(record, exc)
            raise
        self.registry.complete(workload_id)
        result = {
            "workload_id": workload_id,
            "workflow": record.workflow,
            "status": "succeeded",
            "execution": result,
        }
        if registry_report is not None:
            result["registry_report"] = registry_report
        if approval_report is not None:
            result["approval_report"] = approval_report
        if approval_outcomes:
            result["approval_outcomes"] = approval_outcomes
        return result


def runtime_from_environment(persistence_client=None, require_configuration=True):
    """Construct the runtime used by a deployed scheduler."""
    runtime = {}
    if require_configuration:
        from .runtime_config import load
        runtime = load(require=True)
    if persistence_client is None:
        from .persistence_client import PersistenceApiClient
        persistence_client = PersistenceApiClient()
    return WorkflowRuntime(
        registry_from_environment(),
        persistence_client,
        approval_registry_from_environment(),
        {"calendly_url": configured_calendly_url(runtime)} if runtime else None,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Execute one queued workflow workload")
    parser.add_argument("--workload-id", required=True)
    parser.add_argument("--agent-results", required=True, help="JSON mapping of agent names to results")
    args = parser.parse_args(argv)
    try:
        with open(args.agent_results) as handle:
            agent_results = json.load(handle)
        if not isinstance(agent_results, dict):
            raise ValueError("--agent-results must contain an object keyed by agent name")
        result = runtime_from_environment().run(args.workload_id, agent_results)
    except (IOError, ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False, default=lambda value: value.__dict__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
