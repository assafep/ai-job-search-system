"""Translate validated agent decisions into Application Service operations.

This module is deliberately separate from agents. It contains the only
workflow-side logic that turns an agent result into a state mutation, and it
can be run in dry-run mode for test drives.
"""

import base64
import hashlib
import re

from .test_runner import AGENTS, resolve, read_json, validate_output
from .document_generator import ensure_scheduling_link, generate_application_pdf
from application_service.journal_format import normalize_posting_url
from adapters.job_boards.posting_details import (
    posting_content_bytes, posting_artifact_name, parse_saved_posting)
from .event_attempt import with_attempt as _with_attempt


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


# Known ATS/HRIS domain patterns that should not be used as company names.
# When the "company" field contains one of these, it means the job board API
# returned the ATS hostname instead of the actual employer name.
_ATS_DOMAIN_PATTERNS = [
    r"^https?://",
    r"\.oraclecloud\.com",
    r"\.workday\.com",
    r"\.greenhouse\.io",
    r"\.lever\.co",
    r"\.rippling\.com",
    r"\.icims\.com",
    r"\.talentlynx\.com",
    r"\.smartrecruiters\.com",
    r"\.jobsundirect\.com",
    r"\.paradox\.io",
    r"\.ashbyhq\.com",
]

# ATS tenant subdomain patterns: some HRIS platforms use tenant-specific
# subdomains as the company field (e.g., "hibob-52bd16f51e4f4a" for HiBob,
# "fa-extu-saasfaprod1" for Oracle Cloud HCM). These look like "name-hexhash"
# or contain known ATS platform names followed by a dash and an identifier.
_ATS_SUBDOMAIN_PATTERNS = [
    # HiBob tenant subdomains: "hibob-" followed by hex characters
    r"^hibob-[0-9a-f]{8,}$",
    # Oracle Cloud HCM tenant subdomains: alphanumeric segments ending with
    # known Oracle Cloud paths (already caught by .oraclecloud.com, but catch
    # the bare tenant name too: e.g., "fa-extu-saasfaprod1")
    r"^[a-z]{2}-[a-z0-9]{3,}-[a-z0-9]{3,}$",
]

_ATS_RE = re.compile("|".join(_ATS_DOMAIN_PATTERNS + _ATS_SUBDOMAIN_PATTERNS), re.IGNORECASE)


def _normalize_company_name(posting: dict) -> str:
    """Normalize the company name from a job posting, replacing ATS hostnames."""
    company = (posting.get("company") or "").strip()
    if not company:
        return ""
    # If the company field looks like a URL or an ATS domain, fall back to
    # extracting a company hint from the posting URL or description.
    if _ATS_RE.search(company):
        # Try to extract a company name from the description's first sentence
        description = (posting.get("description") or "").strip()
        if description:
            # Take the first sentence, up to 100 chars, as a fallback hint
            first_sentence = description.split(".")[0].strip()
            if len(first_sentence) > 100:
                first_sentence = first_sentence[:100].rstrip() + "..."
            if first_sentence:
                return first_sentence
        # Last resort: use the posting URL path as a hint
        url = posting.get("posting_url", "")
        if url:
            return "Unknown (URL: {})".format(url[:80])
        return "Unknown"
    return company


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
                    "bullets": _with_attempt(["Interview briefing:", result["briefing"]], result.get("attempt")),
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
                "application_received": "Applied",
                "application_confirmed": "Applied",
                "confirmation": "Applied",
                "applied_confirmation": "Applied",
                "offer": "Offer",
            }
            target = target_by_intent.get(intent)
            current = ((context.get('application') or {}).get('status'))
            from application_service.state_machine import ALLOWED_TRANSITIONS
            if target and current and current != target and target not in ALLOWED_TRANSITIONS.get(current, ()):
                target = None
            operations.append({
                "operation": "append_event",
                "application_id": application_id,
                "event_type": "correspondence_classified",
                "data": {"bullets": _with_attempt(bullets, result.get("attempt")),
                    "source": "correspondence agent"},
                "idempotency_key": _idempotency_key(context, "correspondence"),
            })
            if target:
                operations.append({
                    "operation": "transition",
                    "application_id": application_id,
                    "target": target,
                    "data": {"source": "recruitment correspondence", "intent": result["intent"]},
                    "idempotency_key": _idempotency_key(context, "correspondence-state"),
                })
    elif workflow_name == "discover":
        # Discover workflow: no agent, just creates applications in Discovered state
        # from job board sources. Filter by location to match applicant preferences.
        applicant_profile = context.get("applicant_profile") or {}
        preferred_countries = [
            c.upper() for c in (applicant_profile.get("job_search") or {}).get("countries") or []
        ]
        postings = context.get("job_postings", [])
        if 'max_postings' in context:
            postings = postings[:max(0, context['max_postings'])]
        for posting in postings:
            company = _normalize_company_name(posting)
            role = posting.get("role", "")
            posting_url = posting.get("posting_url", "")
            if not posting_url:
                raise ValueError("discover requires posting_url in job_postings")
            # Filter by location: skip postings whose country doesn't match applicant preferences
            posting_countries = [
                c.upper() for c in (posting.get("countries") or [])
                if isinstance(c, str)
            ]
            if preferred_countries:
                if not set(posting_countries) & set(preferred_countries):
                    continue
            operations.append({
                "operation": "create_application",
                "company": company,
                "role": role,
                "posting_url": posting_url,
                "data": {"source": "job discovery", **{
                    key: posting[key] for key in (
                        'source', 'source_id', 'listing_url', 'location', 'countries',
                        'posted_at', 'updated_at', 'publication_date_kind', 'freshness',
                        'matched_skills') if key in posting
                }},
                "target_state": "Discovered",
                "idempotency_key": _idempotency_key(
                    {"workload_id": posting_url}, "discovered-url"),
            })
    elif workflow_name == "bookmark-discovered":
        # Canonical bookmarking only queues the link. Native ChatGPT validation
        # owns website reading. Captured legacy read-posting results remain
        # supported for replay and isolated contract tests.
        posting_result = agent_results.get("read-posting")
        if posting_result is None:
            if not context.get('application_id'):
                raise ValueError('bookmarking requires application_id')
            operations.append({
                'operation': 'transition', 'application_id': context['application_id'], 'target': 'Bookmarked',
                'data': {'source': 'bookmark workflow', 'bullets': [
                    'Bookmarked the discovered link. ChatGPT native browser will read and validate the role description.']},
                'idempotency_key': _idempotency_key(context, 'bookmark-link'),
            })
        if posting_result is not None:
            _validate_agent_result("read-posting", posting_result)
            from .scout_reading import PostingReadFailure, posting_from_model
            application_id = context.get("application_id")
            posting_url = context.get("posting_url") or context.get("read_posting_url")
            if not posting_url:
                raise ValueError("read-posting requires posting_url in context")
            company = posting_result.get("company") or context.get("company") or ""
            role = posting_result.get("role") or context.get("role") or ""
            try:
                posting = posting_from_model(posting_url, company, role, posting_result)
            except PostingReadFailure as exc:
                # URL is inaccessible or posting content unavailable
                operations.append({
                    "operation": "transition",
                    "application_id": application_id,
                    "target": "Dead Letters Queue",
                    "data": {"source": "read-posting agent", "bullets": _with_attempt([str(exc)], getattr(exc, "attempt", None))},
                    "idempotency_key": _idempotency_key(
                        {"workload_id": posting_url}, "read-failure"),
                })
                return operations
            if posting is None:
                raise ValueError("read-posting returned no content")
            operations.append({
                "operation": "store_posting_artifact",
                "application_id": application_id,
                "posting_content": dict(posting, application_id=application_id),
                "idempotency_key": "posting-" + hashlib.sha256(
                    (posting_url + "\n" + posting["description"]).encode("utf-8")).hexdigest()[:24],
            })
            attempt = agent_results.get("bookmark_attempt")
            bookmarked = {
                "source": "read-posting agent",
                "bullets": _with_attempt(["Bookmarked the posting."], attempt),
            }
            if attempt:
                bookmarked["attempt"] = attempt
            operations.append({
                "operation": "transition",
                "application_id": application_id,
                "target": "Bookmarked",
                "data": bookmarked,
                "idempotency_key": _idempotency_key(
                    {"workload_id": posting_url}, "read-to-bookmarked"),
            })
    elif workflow_name == "validate-bookmarked":
        # Validate saved posting content against applicant profile
        result = agent_results.get("validate")
        if result is not None:
            _validate_agent_result("validate", result)
            application_id = context.get("application_id")
            if not application_id:
                raise ValueError("validate requires application_id")
            if result.get("is_match"):
                evidence = result.get("relevance_evidence", [])
                operations.append({
                    "operation": "transition",
                    "application_id": application_id,
                    "target": "Validated",
                    "data": {
                        "source": "validate agent",
                        "bullets": _with_attempt(
                            ["Validated: strong match for applicant"] + evidence, result.get("attempt")),
                    },
                    "idempotency_key": "validate-transition-" + application_id,
                })
            else:
                # Record the decline, then complete the application. Completion
                # copies the journal tab and removes it from the main journal.
                reason = result.get("decline_reason", "Not a strong match")
                operations.append({
                    "operation": "append_event",
                    "application_id": application_id,
                    "event_type": "posting_invalidated",
                    "data": {
                        "bullets": _with_attempt(["Invalidated: " + reason], result.get("attempt")),
                        "source": "validate agent",
                    },
                    "idempotency_key": "invalidate-event-" + application_id,
                })
                operations.append({
                    "operation": "complete_application",
                    "application_id": application_id,
                    "reason": "invalidated",
                    "idempotency_key": "invalidate-complete-" + application_id,
                })
    elif workflow_name == "prepare-validated":
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
                        "source_resume_base64": context.get("source_resume_base64"),
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
                event_bullets = _with_attempt(
                    ["Application package created successfully."], result.get("attempt"))
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
                "data": {"bullets": _with_attempt(bullets, result.get("attempt") if isinstance(result, dict) else None), "source": "reconciliation workflow"},
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
            target_state = operation.get("target_state", "Bookmarked")
            result = persistence_client.create_application(
                operation["company"],
                operation["role"],
                operation["posting_url"],
                target_state=target_state,
                data=operation.get("data"),
                idempotency_key=operation.get("idempotency_key"),
            )
            if result is None or (isinstance(result, dict) and result.get('capacity_exhausted')):
                break
            results.append(result)
            if isinstance(result, dict):
                application_id = result.get("application_id")
                posting_url = result.get("posting_url")
            else:
                application_id = getattr(result, "application_id", None)
                posting_url = getattr(result, "posting_url", None)
            if application_id and posting_url:
                application_ids_by_url[normalize_posting_url(posting_url)] = application_id
            continue
        if operation["operation"] == "store_posting_artifact":
            posting = operation["posting_content"]
            content = posting_content_bytes(posting)
            idem_key = operation.get("idempotency_key") or ""
            artifact = persistence_client.store_artifact(
                operation["application_id"],
                posting_artifact_name(posting.get("company", ""), posting.get("role", ""), posting['posting_url']),
                content,
                "application/json",
                idem_key,
            )
            results.append(artifact)
            drive_file_id = artifact.get("drive_file_id") if isinstance(artifact, dict) else getattr(artifact, "drive_file_id", None)
            artifact_id = artifact.get("artifact_id") if isinstance(artifact, dict) else getattr(artifact, "artifact_id", None)
            reference = ("https://drive.google.com/file/d/{}/view".format(drive_file_id)
                         if drive_file_id and not str(drive_file_id).startswith("memory://")
                         else "artifact://{}".format(artifact_id))
            # The bookmark transition is the only journal event. Its date
            # heading carries the saved-content link with the other bullets.
            note = "Posting content: {}".format(reference)
            for later in operations:
                if later.get("operation") != "transition" or later.get("application_id") != operation["application_id"]:
                    continue
                data = later.setdefault("data", {})
                bullets = [str(item) for item in (data.get("bullets") or []) if str(item).strip()]
                if note not in bullets:
                    model_at = next((index for index, line in enumerate(bullets) if line.startswith("Model:")), len(bullets))
                    bullets.insert(model_at, note)
                data["bullets"] = bullets
                break
            continue
        if operation["operation"] == "generate_and_store_artifact":
            resume_pdf = None
            if operation.get("source_resume_base64"):
                resume_pdf = base64.b64decode(operation["source_resume_base64"], validate=True)
            elif operation.get("source_resume_path"):
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
            if status in ("Bookmarked", "Validated"):
                result = persistence_client.transition(
                    operation["application_id"],
                    "Prepared",
                    {"source": "application package prepared"},
                    operation.get("idempotency_key"),
                )
            else:
                result = application
            results.append(result)
            continue
        if operation["operation"] == "complete_application":
            result = persistence_client.complete_application(
                operation["application_id"], operation["reason"])
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
            note = "Application package: {}".format(reference)
            bullets = list(event_data.get("bullets") or [])
            model_at = next((index for index, line in enumerate(bullets) if str(line).startswith("Model:")), len(bullets))
            if note not in bullets:
                bullets.insert(model_at, note)
            event_data["bullets"] = bullets
        result = persistence_client.append_event(
            operation["application_id"],
            operation["event_type"],
            event_data,
            operation.get("idempotency_key"),
        )
        results.append(result)
    return {"dry_run": False, "operations": operations, "results": results}
