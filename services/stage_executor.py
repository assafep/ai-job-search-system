"""Lifecycle boundary between queued workloads and stateless stage execution."""

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass

from .agent_runner import run as run_agent
from .approval_service import evaluate
from .approval_registry import approval_registry_from_environment
from .runtime_config import calendly_url as configured_calendly_url
from .workload_runner import _get_agents, execute, failure_outcome
from .workload_registry import registry_from_environment
from application_service.profile import calendly_url as profile_calendly_url
from application_service.state_machine import ALLOWED_TRANSITIONS


class StageExecutor:
    """Claim, execute, and settle one workload registry record.

    Agent results are supplied by the caller after the stateless agent has run.
    This class owns only workload bookkeeping; application mutations still go
    through the Application Service client passed to ``run``.
    """

    def __init__(self, registry, persistence_client, approval_registry=None, runtime_context=None):
        self.registry = registry
        self.persistence_client = persistence_client
        self.approval_registry = approval_registry
        self.runtime_context = runtime_context or {}

    def _record_preparation_failure(self, record, error):
        """Route preparation failures through the data layer, with retry-safe history."""
        if record.stage != 'prepare-validated':
            return
        application_id = (record.event.get('payload') or {}).get('application_id')
        if not application_id:
            return
        from .application_service_client import ApplicationServiceError
        try:
            application = self.persistence_client.get_application(application_id)
        except KeyError:
            return  # A deleted posting must not be recreated by failure handling.
        except ApplicationServiceError as exc:
            if 'returned 404:' in str(exc):
                return
            raise
        if application is None:
            return
        state = application.get('status') if isinstance(application, dict) else application.status
        outcome, target = failure_outcome(record.stage, error)
        if state != target and target not in ALLOWED_TRANSITIONS.get(state, set()):
            return  # Never regress a submitted or closed application.
        from .event_attempt import with_attempt
        self.persistence_client.transition(application_id, target, {
            'source': 'application package preparation agent',
            'bullets': with_attempt(
                ['Application package preparation {}: {}'.format(outcome, error)],
                getattr(error, 'attempt', None)),
        }, 'preparation-failed-' + record.workload_id)

    def _fail_workload(self, record, error):
        self.registry.fail(record.workload_id, str(error))
        try:
            self._record_preparation_failure(record, error)
        except Exception as routing_error:
            _outcome, target = failure_outcome(record.stage, error)
            raise RuntimeError('Preparation failed: {}. Recording {} also failed: {}'.format(
                error, target, routing_error)) from routing_error

    def prepare_context(self, stage, context, agent_provider=None):
        """Read all persistence data needed for a stage and return enriched context.

        This is the ONLY method that reads from the Application Service for stage
        execution. It must be called by the main consumer thread before dispatching
        to worker threads.
        """
        enriched = dict(context)
        profile = None
        if hasattr(self.persistence_client, "get_profile"):
            profile = self.persistence_client.get_profile()
            enriched["applicant_profile"] = profile
            if not enriched.get("calendly_url"):
                enriched["calendly_url"] = profile_calendly_url(profile)
        if stage == 'discover':
            from .polling import discovery_capacity
            enriched['max_postings'] = discovery_capacity(self.persistence_client)
            if not enriched['max_postings']:
                enriched['job_postings'] = []
                return enriched
            enriched['results_per_page'] = min(enriched.get('results_per_page', 20), enriched['max_postings'])
        if stage in ("discover", "validate-bookmarked"):
            self._enrich_discovery_context(enriched, profile, agent_provider)
        if stage == "prepare-validated":
            enriched["source_resume_path"] = (profile or {}).get("source_resume", {}).get("path")
            if not enriched["source_resume_path"]:
                raise ValueError("original onboarding resume PDF is required to prepare an application package")
            enriched.pop("cv_html", None)
        application_id = enriched.get("application_id")
        if not application_id:
            return enriched
        if hasattr(self.persistence_client, "get_application"):
            application = self.persistence_client.get_application(application_id)
            enriched["application"] = asdict(application) if is_dataclass(application) else application
        if stage == "prepare-validated":
            from adapters.job_boards.posting_details import read_posting_artifact
            from pypdf import PdfReader
            from io import BytesIO
            import base64
            from .source_resume import read_source_resume
            application = enriched.get("application") or {}
            enriched["job_posting"] = read_posting_artifact(
                self.persistence_client, application_id, application.get('posting_url', ''))
            resume = read_source_resume(self.persistence_client, profile)
            enriched["source_resume_base64"] = base64.b64encode(resume).decode("ascii")
            enriched["resume_text"] = '\n'.join(
                page.extract_text() or '' for page in PdfReader(BytesIO(resume)).pages)
        if stage == "bookmark-discovered":
            application = enriched.get("application") or {}
            if not enriched.get("posting_url") and not enriched.get("read_posting_url"):
                enriched["posting_url"] = application.get("posting_url")
        if stage == "validate-bookmarked":
            from adapters.job_boards.posting_details import read_posting_artifact
            application = enriched.get("application") or {}
            enriched["posting_content"] = read_posting_artifact(
                self.persistence_client, application_id, application.get('posting_url', ''))
        if hasattr(self.persistence_client, "list_events"):
            enriched["application_events"] = self.persistence_client.list_events(application_id)
        if stage == "prepare-interview" and hasattr(self.persistence_client, "list_research"):
            enriched["existing_research"] = self.persistence_client.list_research(application_id)
        return enriched

    def run_agent(self, stage, enriched_context, agent_provider):
        """Invoke the stage's agent(s) on pre-enriched context.

        This is a stateless operation: no persistence reads or writes.
        It is safe to call from worker threads.
        """
        from .workload_runner import _get_agents
        if stage == "bookmark-discovered":
            # Retrieval is an adapter concern, not an LLM capability. Fetch
            # and validate the exact posting before invoking any agent. This
            # handles SPA shells and ATS-specific public data endpoints and
            # guarantees that the saved artifact does not depend on the
            # coding agent having browser/network access.
            from .bookmark_read import read_discovered_posting
            posting_url = (enriched_context.get("posting_url") or
                           enriched_context.get("read_posting_url"))
            if not posting_url:
                raise ValueError("bookmark-discovered requires posting_url")
            output, attempt = read_discovered_posting(
                posting_url, enriched_context.get("company"), enriched_context.get("role"),
                provider=agent_provider)
            enriched_context["retrieved_posting"] = output
            result = {"read-posting": output}
            if attempt:
                result["bookmark_attempt"] = attempt
            return result
        agent_results = {}
        for agent_name in _get_agents(stage):
            agent_context = {key: value for key, value in enriched_context.items()
                             if key != "source_resume_base64"}
            if hasattr(agent_provider, "with_reasoning_effort"):
                from .workload_effort import effort_for_stage
                agent_context["reasoning_effort"] = effort_for_stage(stage)
                agent_provider = agent_provider.with_reasoning_effort(agent_context["reasoning_effort"])
            agent_results[agent_name] = run_agent(agent_name, agent_context, agent_provider)
        return agent_results

    @staticmethod
    def _enrich_discovery_context(context, profile=None, agent_provider=None):
        """Fetch source postings before invoking the discovery agent.

        The source adapter is deliberately optional for replay and tests. A
        scheduled event explicitly naming Adzuna must have credentials and a
        query, otherwise failing here is safer than producing fabricated jobs.
        """
        source_names = context.get("source_names") or []
        source = next((name for name in (
            "freehire", "jobicy", "adzuna", "greenhouse", "ashby", "comeet", "lever", "himalayas",
            "smartrecruiters",
        ) if name in source_names), None)
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
        # Resolve preferred countries from the applicant profile for all adapters.
        # Context override > profile job_search.countries > location codes > default IL.
        preferred_countries = context.get("countries") or job_search.get("countries") or [
            item.upper() for item in locations if isinstance(item, str) and len(item) == 2
        ] or ["IL"]
        from adapters.job_boards.framework import get_framework
        from adapters.job_boards.interface import DiscoveryContext
        from .discovery_sources import source_boards, select_live_postings, fetch_employer_boards
        framework = get_framework()
        request = DiscoveryContext(
            query=query, countries=preferred_countries, locations=locations,
            limit=int(context.get('results_per_page', 20 if source == 'adzuna' else 100)),
            page=int(context.get('page', 1)), offset=int(context.get('offset', 0)))
        if source in {'greenhouse', 'ashby', 'comeet', 'lever', 'smartrecruiters'}:
            boards = source_boards(source)
            if context.get('discovery_board'):
                token = context['discovery_board']
                if token not in boards:
                    raise ValueError('Discovery board is not in the configured source catalog')
                boards = {token: boards[token]}
            postings, errors = fetch_employer_boards(source, boards, framework=framework)
            context['job_postings'] = select_live_postings(postings, profile, preferred_countries)
            if errors:
                context['discovery_source_errors'] = errors
            return
        if source in {'himalayas', 'jobicy'}:
            from dataclasses import replace
            queries = [str(term).strip() for term in terms if str(term).strip()]
            if source == 'himalayas':
                queries = (queries or ['backend', 'java', 'scala', 'python', 'data engineer'])[:6]
            else:
                queries = queries or ['']
            collected, errors, succeeded = {}, [], 0
            for term in queries:
                result = framework.search(replace(request, query=term), source)
                if result.success:
                    succeeded += 1
                    for posting in result.data:
                        collected[posting['posting_url']] = posting
                else:
                    errors.append('{}: {}'.format(term, result.error))
            if errors and not succeeded:
                raise RuntimeError('; '.join(errors))
            postings = list(collected.values())
            context['job_postings'] = (select_live_postings(postings, profile, preferred_countries)
                                       if source == 'himalayas' else postings)
            if errors:
                context['discovery_source_errors'] = errors
            return
        if source == 'adzuna' and not query and not where:
            raise ValueError('Adzuna discovery requires search_terms or locations')
        context['job_postings'] = framework.search(request, source).unwrap()

    def run(self, workload_id, agent_results=None, agent_provider=None, recover_incomplete=True):
        # A reconciliation tick is the recovery boundary for interrupted
        # workloads. Reset recoverable running records before claiming the
        # reconciliation workload itself; application mutations remain under
        # the Application Service.
        record = self.registry.get(workload_id)
        if record.stage in {'identify-ghosting', 'renew-gmail-grant', 'complete-journal'}:
            from .ghosting_stage import execute as execute_ghosting
            from .gmail_grant_stage import execute as execute_grant
            from .completion_stage import execute as execute_completion
            handlers = {'identify-ghosting': execute_ghosting,
                        'renew-gmail-grant': execute_grant,
                        'complete-journal': execute_completion}
            self.registry.claim(workload_id)
            try:
                results = handlers[record.stage](self.persistence_client)
            except Exception as exc:
                self.registry.fail(workload_id, str(exc))
                raise
            self.registry.complete(workload_id)
            return {'workload_id': workload_id, 'stage': record.stage,
                    'status': 'succeeded', 'results': results}
        previous_error = record.error
        if record.stage == 'prepare-validated' and agent_results is None and hasattr(self.persistence_client, 'list_artifacts'):
            from .application_package import saved_package, promote_saved_package
            self.registry.claim(workload_id)
            try:
                app = self.persistence_client.get_application((record.event.get('payload') or {})['application_id'])
                if app is not None and not isinstance(app, dict):
                    app = asdict(app)
                if app['status'] != 'Validated':
                    _outcome, target = failure_outcome(record.stage, previous_error or RuntimeError('preparation failed'))
                    if app['status'] == target and previous_error:
                        self._record_preparation_failure(record, previous_error)
                    self.registry.complete(workload_id)
                    return {'stage': record.stage, 'workload_id': workload_id, 'status': 'succeeded',
                            'application_id': app['application_id'],
                            'activity': 'Application is already {}; no package regeneration is needed.'.format(app['status'])}
                artifact = saved_package(self.persistence_client, app)
                if artifact:
                    result = promote_saved_package(self.persistence_client, app, artifact)
                    self.registry.complete(workload_id)
                    return {'stage': record.stage, 'workload_id': workload_id, 'status': 'succeeded',
                            'application_id': app['application_id'], 'activity': 'Reused saved package and promoted automatically.',
                            'package_reference': artifact['reference'], 'application': result}
                # Continue the normal generation path without a second claim.
                preclaimed = True
            except Exception as exc:
                self._fail_workload(record, exc)
                raise
        else:
            preclaimed = False
        if record.stage == 'submit-prepared':
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
                return {'workload_id': workload_id, 'stage': 'submit-prepared',
                        'status': self.registry.get(workload_id).status, 'submission': outcome}
            except Exception as exc:
                self.registry.fail(workload_id, str(exc))
                raise
        registry_report = None
        approval_report = None
        if record.stage == "reconcile":
            if recover_incomplete:
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
                    from .workload_effort import effort_for_stage
                    context["reasoning_effort"] = effort_for_stage(record.stage)
                    agent_provider = agent_provider.with_reasoning_effort(context["reasoning_effort"])
                context = self.prepare_context(record.stage, context, agent_provider)
                agent_results = {}
                for agent_name in _get_agents(record.stage):
                    agent_context = {key: value for key, value in context.items()
                                     if key != "source_resume_base64"}
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
                        {"stage": record.stage, "context": context},
                        "{}:approval:{}".format(record.workload_id, outcome["action"]),
                    )
                    outcome["request"] = asdict(request)
            result = execute(
                record.stage,
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
            "stage": record.stage,
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
        from .application_service_client import ApplicationServiceClient
        persistence_client = ApplicationServiceClient()
    return StageExecutor(
        registry_from_environment(),
        persistence_client,
        approval_registry_from_environment(),
        {"calendly_url": configured_calendly_url(runtime)} if runtime else None,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Execute one queued stage workload")
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
