"""Translate validated agent decisions into persistence API operations.

This module is deliberately separate from agents. It contains the only
workflow-side logic that turns an agent result into a state mutation, and it
can be run in dry-run mode for test drives.
"""

import hashlib
import re

from .test_runner import AGENTS, resolve, read_json, validate_output
from .document_generator import ensure_scheduling_link, generate_application_pdf
from persistence_api.journal_format import normalize_posting_url
from adapters.job_boards.posting_details import fetch_posting_details, posting_content_bytes, posting_artifact_name


SCHEDULING_INTENTS = {
    "schedule_interview",
    "interview_scheduled",
    "request_interview",
    "request_another_meeting",
    "another_interview",
    "reschedule_interview",
    "reschedule",
    "change_interview_time",
    "change_time",
}


def _idempotency_key(context, suffix):
    seed = "{}:{}".format(context.get("workload_id") or context.get("trigger", {}).get("event_id", "manual"), suffix)
    return "workload-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _package_allows_ready(package):
    """No human-review gate: successful package storage is the readiness gate."""
    return bool(str(package.get('cover_letter') or '').strip())


def _application_pdf_name(context):
    configured = context.get("application_pdf_name")
    if configured:
        return configured
    application = context.get("application") or {}
    company = application.get("company", "Application")
    role = application.get("role", "Package")
    personal = (context.get("applicant_profile") or {}).get("personal") or {}
    applicant = personal.get("name") or personal.get("full_name") or "Applicant"
    label = "{} Cover Letter & Resume - {} - {}.pdf".format(applicant, company, role)
    return re.sub(r'[\\/:*?"<>|]+', "-", label).strip()


def _validate_agent_result(agent_name, result):
    schema = read_json(resolve(AGENTS[agent_name] + "/output.schema.json"))
    errors = validate_output(result, schema)
    if errors:
        raise ValueError("invalid {} result: {}".format(agent_name, "; ".join(errors)))


def plan_persistence_writes(workflow_name, context, agent_results):
    """Return explicit persistence operations without executing them."""
    operations = []
    if workflow_name == "prepare-interview":
        result = agent_results.get("interview-prep")
        if result is not None:
            _validate_agent_result("interview-prep", result)
            application_id = context.get("application_id")
            if not application_id:
                raise ValueError("prepare-interview requires application_id")
            operations.append({
                "operation": "transition",
                "application_id": application_id,
                "target": "Interviewing",
                "data": {"source": "calendar interview detected"},
                "idempotency_key": _idempotency_key(context, "interview-state"),
            })
            operations.append({
                "operation": "write_research",
                "application_id": application_id,
                "title": "Research — Interview Briefing",
                "content": result["briefing"],
                "idempotency_key": _idempotency_key(context, "interview-research"),
            })
            operations.append({
                "operation": "append_event",
                "application_id": application_id,
                "event_type": "interview_briefing_created",
                "data": {
                    "bullets": ["Interview briefing:", result["briefing"]],
                    "source": "interview-prep agent",
                },
                "idempotency_key": _idempotency_key(context, "interview-briefing"),
            })
    elif workflow_name == "handle-incoming-email":
        result = agent_results.get("correspondence")
        if result is not None:
            _validate_agent_result("correspondence", result)
            application_id = context.get("application_id")
            if not application_id:
                raise ValueError("handle-incoming-email requires application_id")
            bullets = ["Intent: " + result["intent"], "Proposed action: " + result["proposed_action"]]
            intent = result["intent"].strip().lower()
            draft = result.get("draft")
            if intent in SCHEDULING_INTENTS:
                draft = ensure_scheduling_link(
                    draft or "Please use my scheduling link to choose a convenient time.",
                    context.get("calendly_url"),
                )
            if draft:
                bullets.append("Draft response: " + draft)
            operations.append({
                "operation": "append_event",
                "application_id": application_id,
                "event_type": "correspondence_classified",
                "data": {"bullets": bullets, "source": "correspondence agent"},
                "idempotency_key": _idempotency_key(context, "correspondence"),
            })
            target_by_intent = {
                "schedule_interview": "Interviewing",
                "interview_scheduled": "Interviewing",
                "request_interview": "Interviewing",
                "request_another_meeting": "Interviewing",
                "another_interview": "Interviewing",
                "reschedule_interview": "Interviewing",
                "reschedule": "Interviewing",
                "change_interview_time": "Interviewing",
                "change_time": "Interviewing",
                "rejection": "Rejected",
                "rejected": "Rejected",
                "offer": "Offer",
            }
            target = target_by_intent.get(result["intent"].strip().lower())
            if target:
                operations.append({
                    "operation": "transition",
                    "application_id": application_id,
                    "target": target,
                    "data": {"source": "recruitment correspondence", "intent": result["intent"]},
                    "idempotency_key": _idempotency_key(context, "correspondence-state"),
                })
    elif workflow_name in ("discover-and-apply", "discover-and-bookmark", "prepare-bookmarked"):
        discovery = agent_results.get("job-discovery")
        if discovery is not None:
            _validate_agent_result("job-discovery", discovery)
            for candidate in discovery["candidates"]:
                for field in ("company", "role", "posting_url"):
                    if not candidate.get(field):
                        raise ValueError("job-discovery candidate is missing " + field)
                operations.append({
                    "operation": "create_application",
                    "company": candidate["company"],
                    "role": candidate["role"],
                    "posting_url": candidate["posting_url"],
                    "data": {"relevance_evidence": candidate.get("relevance_evidence", [])},
                    "capture_posting_content": workflow_name == "discover-and-bookmark",
                    "idempotency_key": _idempotency_key(
                        {"workload_id": candidate["posting_url"]}, "discovered-application"),
                })
        if workflow_name == "discover-and-bookmark":
            return operations
        result = agent_results.get("application")
        if result is not None:
            _validate_agent_result("application", result)
            packages = result.get("packages")
            if packages is None:
                packages = [dict(result, **({
                    "posting_url": context.get("posting_url"),
                    "application_id": context.get("application_id"),
                }))]
            for package in packages:
                if not str(package.get("cover_letter") or "").strip():
                    raise ValueError("application agent did not produce a grounded cover letter")
                application_id = package.get("application_id") or context.get("application_id")
                posting_url = package.get("posting_url") or context.get("posting_url")
                if not application_id and not posting_url:
                    raise ValueError("application package requires application_id or posting_url")
                target = {"application_id": application_id} if application_id else {
                    "application_ref": {"posting_url": posting_url}
                }
                package_artifact_reference = context.get("existing_application_package_reference")
                package_generation_planned = False
                if (context.get("source_resume_path") or context.get("cv_html")) and not context.get("existing_application_package"):
                    package_generation_planned = True
                    operations.append(dict({
                        "operation": "generate_and_store_artifact",
                        **target,
                        "name": _application_pdf_name(context),
                        "cover_letter": package["cover_letter"],
                        "cv_html": context.get("cv_html"),
                        "source_resume_path": context.get("source_resume_path"),
                        "applicant_profile": context.get("applicant_profile"),
                        "application": context.get("application"),
                        "calendly_url": context.get("calendly_url"),
                        "mime_type": "application/pdf",
                        "idempotency_key": _idempotency_key(
                            {"workload_id": context.get("workload_id") or posting_url},
                            "application-pdf-{}".format(posting_url or application_id),
                        ),
                    }))
                # A package-prepared event is valid only after a package was
                # stored (or an already stored package was supplied).
                if not package_generation_planned and not package_artifact_reference:
                    if not context.get("existing_application_package"):
                        continue
                    raise ValueError("existing application package has no Drive reference")
                event_bullets = ["Application package created successfully."]
                operations.append(dict({
                    "operation": "append_event",
                    **target,
                    "event_type": "application_package_prepared",
                    "data": {
                        "bullets": event_bullets,
                        **({"artifact_reference": package_artifact_reference} if package_artifact_reference else {}),
                        "source": "application agent",
                    },
                    "idempotency_key": _idempotency_key(
                        {"workload_id": context.get("workload_id") or posting_url},
                        "application-package-{}".format(posting_url or application_id),
                    ),
                }))
                # Promotion is deliberately last. If PDF generation,
                # artifact storage, or journal recording fails, the posting
                # remains Bookmarked and will be retried by the daemon.
                if _package_allows_ready(package):
                    operations.append({
                        "operation": "ensure_ready_to_apply",
                        **target,
                        "idempotency_key": _idempotency_key(
                            {"workload_id": context.get("workload_id") or posting_url},
                            "application-ready-{}".format(posting_url),
                        ),
                    })
                if context.get("submission_confirmed") is True and _package_allows_ready(package):
                    submission_operation = {
                        "operation": "transition",
                        "target": "Applied",
                        "data": {"source": "external submission confirmed"},
                        "idempotency_key": _idempotency_key(
                            {"workload_id": context.get("workload_id") or posting_url},
                            "application-submitted-{}".format(posting_url or application_id),
                        ),
                    }
                    submission_operation.update(target)
                    operations.append(submission_operation)
    elif workflow_name == "reconcile":
        # Registry recovery is performed by WorkflowRuntime. If the
        # reconciliation event is associated with an application, persist the
        # external-status observation through the same API boundary as every
        # other workflow. A scheduled reconciliation with no application
        # context remains a valid no-op after registry recovery.
        application_id = context.get("application_id")
        if application_id:
            result = context.get("reconciliation_result") or {}
            if isinstance(result, str):
                result = {"summary": result}
            if not isinstance(result, dict):
                raise ValueError("reconciliation_result must be an object or string")
            bullets = ["Reconciliation completed."]
            for key in ("summary", "external_status", "pending_approval", "error"):
                if result.get(key) is not None:
                    bullets.append("{}: {}".format(key.replace("_", " ").title(), result[key]))
            operations.append({
                "operation": "append_event",
                "application_id": application_id,
                "event_type": "reconciliation_completed",
                "data": {"bullets": bullets, "source": "reconciliation workflow"},
                "idempotency_key": _idempotency_key(context, "reconciliation-event"),
            })
            target = result.get("target_status")
            if target:
                operations.append({
                    "operation": "transition",
                    "application_id": application_id,
                    "target": target,
                    "data": {"source": "reconciliation workflow"},
                    "idempotency_key": _idempotency_key(context, "reconciliation-state"),
                })
    return operations


def execute_persistence_writes(operations, persistence_client, dry_run=True):
    """Execute only explicit operations; return the same operations in dry-run mode."""
    if dry_run:
        return {"dry_run": True, "operations": operations}
    results = []
    application_ids_by_url = {}
    package_artifacts = {}
    for operation in operations:
        operation = dict(operation)
        reference = operation.pop("application_ref", None)
        if reference:
            posting_url = normalize_posting_url(reference["posting_url"])
            operation["application_id"] = application_ids_by_url.get(posting_url)
            if not operation["application_id"]:
                raise ValueError("no created application for posting URL: " + reference["posting_url"])
        if operation["operation"] == "create_application":
            result = persistence_client.create_application(
                operation["company"],
                operation["role"],
                operation["posting_url"],
                operation.get("data"),
                operation.get("idempotency_key"),
            )
            results.append(result)
            if isinstance(result, dict):
                application_id = result.get("application_id")
                posting_url = result.get("posting_url")
            else:
                application_id = getattr(result, "application_id", None)
                posting_url = getattr(result, "posting_url", None)
            if application_id and posting_url:
                application_ids_by_url[normalize_posting_url(posting_url)] = application_id
            if operation.get('capture_posting_content'):
                details = fetch_posting_details(posting_url)
                artifact = persistence_client.store_artifact(
                    application_id,
                    posting_artifact_name(operation['company'], operation['role']),
                    posting_content_bytes(details),
                    'application/json',
                    (operation.get('idempotency_key') or '') + '-posting-content',
                )
                drive_file_id = artifact.get('drive_file_id') if isinstance(artifact, dict) else getattr(artifact, 'drive_file_id', None)
                artifact_id = artifact.get('artifact_id') if isinstance(artifact, dict) else getattr(artifact, 'artifact_id', None)
                reference = ('https://drive.google.com/file/d/{}/view'.format(drive_file_id)
                             if drive_file_id and not str(drive_file_id).startswith('memory://')
                             else 'artifact://{}'.format(artifact_id))
                event = persistence_client.append_event(
                    application_id, 'posting_content_saved',
                    {'bullets': ['Posting content: {}'.format(reference)], 'source': 'job discovery agent'},
                    (operation.get('idempotency_key') or '') + '-posting-content-event')
                results.extend([artifact, event])
            continue
        if operation["operation"] == "generate_and_store_artifact":
            resume_pdf = None
            if operation.get("source_resume_path"):
                with open(operation["source_resume_path"], "rb") as source:
                    resume_pdf = source.read()
            content = generate_application_pdf(
                operation["cover_letter"],
                operation["cv_html"],
                operation.get("calendly_url"),
                application=operation.get('application'),
                **({"resume_pdf": resume_pdf, "applicant_profile": operation.get("applicant_profile")}
                   if resume_pdf is not None else {}),
            )
            result = persistence_client.store_artifact(
                operation["application_id"],
                operation["name"],
                content,
                operation["mime_type"],
                operation.get("idempotency_key"),
            )
            results.append(result)
            if operation.get("application_id"):
                package_artifacts[operation["application_id"]] = result
            continue
        if operation["operation"] == "ensure_ready_to_apply":
            application = persistence_client.get_application(operation["application_id"])
            if application is None:
                raise KeyError(operation["application_id"])
            status = application.get("status") if isinstance(application, dict) else getattr(application, "status", None)
            if status == "Bookmarked":
                result = persistence_client.transition(
                    operation["application_id"],
                    "Ready for Auto-Apply",
                    {"source": "application package prepared"},
                    operation.get("idempotency_key"),
                )
            else:
                result = application
            results.append(result)
            continue
        if operation["operation"] == "transition":
            result = persistence_client.transition(
                operation["application_id"],
                operation["target"],
                operation.get("data"),
                operation.get("idempotency_key"),
            )
            results.append(result)
            continue
        if operation["operation"] == "write_research":
            result = persistence_client.write_research(
                operation["application_id"],
                operation["title"],
                operation["content"],
                operation.get("idempotency_key"),
            )
            results.append(result)
            continue
        if operation["operation"] != "append_event":
            raise ValueError("unsupported persistence operation: " + operation["operation"])
        event_data = dict(operation.get("data") or {})
        if operation.get("event_type") == "application_package_prepared":
            artifact = package_artifacts.get(operation["application_id"])
            reference = event_data.get("artifact_reference")
            if not reference and artifact is not None:
                drive_file_id = artifact.get("drive_file_id") if isinstance(artifact, dict) else getattr(artifact, 'drive_file_id', None)
                artifact_id = artifact.get('artifact_id') if isinstance(artifact, dict) else getattr(artifact, 'artifact_id', None)
                if drive_file_id:
                    reference = "https://drive.google.com/file/d/{}/view".format(drive_file_id)
                elif artifact_id:
                    reference = str(artifact_id)
            if not reference:
                raise ValueError("application package event requires a stored artifact")
            event_data.pop("artifact_reference", None)
            event_data["bullets"] = list(event_data.get("bullets") or []) + [
                "Application package: {}".format(reference)
            ]
        result = persistence_client.append_event(
            operation["application_id"],
            operation["event_type"],
            event_data,
            operation.get("idempotency_key"),
        )
        results.append(result)
    return {"dry_run": False, "operations": operations, "results": results}
