---
name: application-service
description: Use the Application Service to read or mutate application state, journal events, research tabs, applicant profiles, and Drive-backed artifacts without accessing Google Drive directly.
---

# Application Service skill

Use this skill whenever a workload needs application state or Drive-backed
files. The machine-readable contract is [`../../openapi.yaml`](../../openapi.yaml);
the implementation is selected behind the HTTP API and may be Google Drive or
the in-memory test backend.

## MCP transport

An operator or workload runtime may access the same domain boundary through the
service's `/mcp` endpoint (production: `https://ajss-one.vercel.app/mcp`). It uses
Streamable HTTP with OAuth or service bearer authentication. Tool names come
from OpenAPI `operationId` values; path/query parameters are top-level arguments,
and JSON bodies use `body`. Keyed writes require a non-empty `idempotency_key`.
Start with the read-only `journalQueueCounts` tool and empty arguments.

The endpoint exposes domain operations and the workflow tools `listWorkflows`,
`startWorkflow`, `getWorkflowRun`, and `getWorkflowRunReport`. `startWorkflow`
enqueues a durable request; a separately provisioned worker executes existing
Python workflows. Connecting MCP does not provision or start that worker.
Use `AJSS_MCP_TOOL_PROFILE=workflows` for ChatGPT workflow control without direct
state mutations. Omitted limits process all eligible postings; an application ID
is resolved before execution. Never retry an interrupted/uncertain running claim;
an operator must stop its worker and reconcile submissions first. Workflow
agents remain stateless decision-makers and must not call MCP tools themselves.
See [the endpoint reference](../../application_service/README.md#mcp-endpoint) and
[connection setup](../../deployment/README.md#mcp-connections).

## Non-negotiable boundary

- Agents are stateless decision-makers. They receive serialized context and
  return structured results; they do not call this API themselves.
- Workload runtimes use `services.application_service_client.ApplicationServiceClient` (or
  an equivalent client generated from OpenAPI).
- Never give an agent a Drive, Docs, Gmail, Calendar, browser, or repository
  client. Never import a Drive adapter into an agent.
- All persistent mutations must be explicit, validated, and retry-safe. Do not
  silently substitute an in-memory backend when the configured backend is
  unavailable.

## Journal model

The canonical application states are `Bookmarked`, `Prepared`,
`Manual Apply Required`, `Dead Letters Queue`, `Applied`, `Interviewing`, `Rejected`, and `Offer`.
Every posting in `Prepared` is authorized for automatic submission;
do not create or wait for a human approval request for that queue. Ordinary failed
auto-apply attempts move the posting to the manual queue, with an explanatory
transition event attributed to the auto-apply agent. Unconfirmed submission
requires manual reconciliation before any retry. Successful package storage advances
Bookmarked postings automatically; no human-review gate is allowed before
Prepared. Automatic submission must not consume the manual queue.
System errors and failed Bookmarked-package preparation transition to Dead Letters Queue,
with an explanatory event and a deterministic idempotency key. Never recreate
deleted postings or regress Applied/Interviewing/terminal states on failure.
The Interviews Journal has one
top-level tab for each state and one application child tab under its current
state. Nested research tabs are children of an application and are not
applications.
System errors before submission move to Dead Letters Queue. Ordinary application blockers still require manual application. Unconfirmed
submission takes precedence over system-error routing and requires manual
reconciliation. Dead letters are excluded from automatic processing.

Use `POST /journal/migrate-application-states` for an explicitly requested
legacy lifecycle migration. It renames `Ready To Apply` in place, preserving
children and history, and adds `Manual Apply Required` and `Dead Letters Queue`. It refuses ambiguous
old/new duplicate state containers; never merge or delete those automatically.

Use the API to:

- read applications, events, existing research, and artifacts;
- create or transition applications using the allowed lifecycle transitions;
- append dated events with non-empty bullet notes;
- create/read research tabs through `/applications/{id}/research`;
- upload/read application artifacts or root-relative files; and
- read or update the applicant profile through `/profile`.

Before a live workload, call `/journal/status` when journal integrity matters.
Do not repair missing state tabs implicitly; `/journal/repair` is an explicit
operator action. Never delete or merge duplicate state tabs automatically.

## Mutation rules

Use a deterministic `idempotency_key` for every retryable create, transition,
event, research, artifact, or file operation. A retry must reconcile the
existing result rather than append a duplicate human-visible journal event.
Read the application and its recent events before constructing a transition or
correspondence update. Keep human notes and automated notes in the same dated
paragraph-plus-bullets journal format.

Application artifacts belong in the single-level `Company / Role` folder under
the configured Drive root (optionally below `application_root_path`). Do not
create company and role as nested folders, and do not write artifact files
directly with a Drive client. Record an artifact reference through the API only
after the upload operation is accepted.

Applicant profile data belongs at `Applicant/applicant_profile.json`
and remains accessible through `/profile`. Store workload-owned internal files
under `.ai-job-search-system/workloads-internal-state/<workflow>/` using `/files`.
Use `application_service.storage_layout.workload_state_path` to construct those paths.
In hybrid installations explicitly configured with `AJSS_WORKLOAD_FILE_BACKEND=database`,
these workflow-owned paths are stored in PostgreSQL behind the same `/files` API;
legacy Drive files remain readable. Never bypass the API or switch backends on
an individual write failure.
Use `POST /storage/migrate-internal` only for an explicitly requested migration.

## Runtime and test modes

Live execution requires completed onboarding and a running Application Service. If
`config/runtime.json` is absent or invalid, stop and direct the operator to:

```bash
python3 -m services.onboarding
```

For side-effect-free validation, use the targeted contract runners:

```bash
python3 -m services.test_runner agent <agent> --fixture <input.json> [--output <result.json>]
python3 -m services.workload_runner <workflow> --fixture <input.json> [--agent-results <results.json>]
```

These commands must not contact Drive or mutate application state. Use
`--execute` only at the explicit workload-runtime boundary after onboarding
checks have succeeded. Ready queue membership authorizes its submission action.

## Bulk mutations

Use `ApplicationServiceClient.bulk_update_items` for multiple explicit item
updates. `POST /bulk-updates` accepts up to 25 typed operations with one
idempotency key per item; the client chunks larger lists without limiting the
requested scope. Inspect all per-item results even when HTTP status is 200.
`services.bulk_updates.require_success` raises a workflow error on any failed or
uncertain item. Reuse item keys and the same payload on retries. Never bypass an
uncertain item with a new key before manual reconciliation.

Workflows 8, 9, and 10 use this interface for their item writes. Terminal cleanup
must select from `/journal/terminal-items`, which excludes archived entries.
Ghosting completion carries the observed activity timestamp and expected state;
Gmail notice acknowledgement carries the grant expiry observed before sending.
