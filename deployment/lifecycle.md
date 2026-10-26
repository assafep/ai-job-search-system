# Provisioning and deployment lifecycle

Design and provider review: 2026-10-24. Operator guide:
[README.md](README.md). Run `./deployment/ctl.sh`.

Provisioning ensures resources exist. Deployment installs a release. Activation
enables serving or work consumption. `plan` shows this contract and changes
nothing. `drive` and `up` have live implementations covering host provisioning,
Drive binding, applicant preparation, application deployment, and MCP connection
verification. Docker runner deployment now creates an image, persistent volume,
and stopped container; activation remains separate. The live `start`, `stop`, and `status` commands control only this existing Docker runner. `start` checks API/worker readiness; `stop` drains without force-killing on timeout. Application pause/resume in the broader contract below remains unimplemented. Use `--setup
local-docker` in non-interactive sessions.
The launcher selects Python 3.9+ (this workstation's default `python3` is 3.7).

## Repository findings

PostgreSQL roles and DDL use repository-owned adapters in
`provisioning/bootstrap`. They save private state under this repository's
`.onboarding` directory and do not require another checkout. Host provisioning
ensures the database; application deployment publishes the Python Application
Service directly. No bootstrap probe is deployed. Private state must never enter
a build or version control.

The main repository's `application_service/main.py` serves `openapi.yaml` through
Python's `HTTPServer`; storage is Google Docs/Drive or memory. It has no PostgreSQL
repository or database driver. `AJSS_DATABASE_URL` alone cannot enable SQL
persistence. Its authentication reads `APPLICATION_SERVICE_TOKEN`, while the bootstrap
exports `AJSS_API_TOKEN`. Cloud deployment must map that name and fail if the
token is absent. `/ready` currently confirms repository initialization, not a
full database/Drive/authentication test.

The application service is the API. Stage instances execute in the separately
hosted stage runner and call that API over HTTP. The runner has a
process/container lifecycle and an independently selected host.

`services.orchestrator`, the workload/approval registries and
`services.orchestration_lock` rely on process-local synchronization and local
files. The default agent invokes a locally authenticated CLI; submission uses
Chrome/Playwright. The existing Docker image installs Python dependencies only.
It does not yet package the browser, Node dependencies, agent authentication,
durable runner storage, or a PostgreSQL service.

`services.onboarding` collects personal preferences and references to an existing
Drive folder/journal. It does not perform Google browser OAuth, provision a
service account, or create the journal. Infrastructure onboarding should finish
before handing off to that profile stage. Avoid prompting twice for values
already present, and never overwrite existing profile or runtime configuration.

## Provider capabilities and required changes

| Area | Verified capability | Contract consequence |
| --- | --- | --- |
| Database | PostgreSQL is the managed database; compute may suspend while idle. | `provision_database` promises a reachable database, not an always-running process. Select database and compute providers independently. |
| Database identities | A PostgreSQL user is a role with `LOGIN`. Neon SQL-created roles avoid the privileged membership of API-created roles. | Create/reconcile owner/migrator and restricted app roles during provisioning; apply table grants/default privileges after DDL too. Never give the runner SQL credentials. |
| Schema | The bootstrap DDL is PostgreSQL-specific but independent of hosting vendor. `IF NOT EXISTS` does not migrate an existing table definition. | Common `sql/schema.sql` is an initial baseline. Future changes require numbered, checksummed migrations and a migration ledger; schema drift is an error, never an automatic reset. |
| Application host | The host reserves a deployment target; it does not start a server. Deployments are immutable releases. | Fingerprint code/config/secret versions and reuse a matching release; stage a production release before activation. |
| Application start/stop | The API can be started and stopped. | Map API start/stop to production availability. Runner start/stop controls its separate process/container. |
| Python API | The service uses Python with standard HTTPServer. | Add a supported entrypoint and routing, explicit config/secrets, dependency packaging and authentication tests. |
| Runner hosting | The current runner is a continuous process with local locks/files. | A single process/container host needs persistent storage and the existing synchronization. Multiple runner hosts would additionally require shared claims/leases/checkpoints; that is a separate scaling change. Package agent/browser dependencies for the selected host. |
| Docker | Compose can create stopped containers and start/stop them; `up` reconciles changed configuration. | Use stable project/service names, persistent named volumes, explicit image/config fingerprints and health checks. Provision Docker engine/host as a prerequisite; local does not imply empty hooks. |
| Personal Google Drive | Service accounts have no storage quota and cannot own newly uploaded files. | Use user OAuth for My Drive. `drive.file` limits access to app-created/selected files, **not to one folder**. Enforce the configured root in the Application Service too. |
| Workspace Drive | Service accounts can create content in Shared Drives, subject to sharing and organizational policy. | Require a Shared Drive, Google Cloud project, IAM API and service-account creation permission. Grant access to the intended folder without broad drive membership where policy permits. Verify inherited permissions and effective capabilities. |
| Google consent | Browser login does not create a Cloud project, OAuth client, consent configuration, or IAM privileges. | Supply a configured OAuth client; use browser authorization with state/PKCE and offline refresh. Persist credentials privately. Testing-mode refresh tokens can expire after seven days. |

Service-account folder sharing is a current ACL grant, not an immutable OAuth
restriction: future sharing can expand access. Domain-wide delegation is not a
folder restriction either and is not a default. Shared Drive support and
service-account token acquisition are missing from the current Drive adapter.
The personal OAuth and Workspace service-account paths are explicit alternatives;
there is no silent fallback from one security model to another.

Initial setup choice is `local-docker` (PostgreSQL, API and runner in Docker).
Docker is the initial runner implementation; the runner host can later be
another local or cloud process/container provider.
Both profiles remain **contract-only** in the new orchestrator. A provider may return
`unchanged` only after observing the requested postcondition; unsupported hooks
must return `unsupported`, never an empty successful function.

## Inputs, outputs and dependencies

Every task receives an installation/environment identity, provider selection,
operation key, desired-spec digest, checkpoint store and secret resolver through
its execution context. Resource IDs and public endpoints are ordinary outputs;
credentials are opaque, versioned secret references. Provider metadata belongs
in provider-owned state, not portable application configuration.

| Task | Additional inputs | Outputs / postcondition | Prerequisites |
| --- | --- | --- | --- |
| `provision_application_host` | host spec: account, region, stable resource name | application `HostTarget`; deploy permissions verified | preflight and authorization |
| `provision_database` | database spec: PostgreSQL version/name, app role; optional host binding | `DatabaseAccess`: resource/database identity, app and migrator secret refs; connectivity and identity privileges checked | no host dependency |
| `apply_database_schema` | `DatabaseAccess`, schema/migration artifact and checksum | `SchemaState`: applied version/checksum and verified app grants | database |
| `provision_drive` | auth mode, credential ref, parent/shared-drive ID, stable folder key; optional Cloud project | `DriveAccess`: folder ID, principal, credential ref, authorization model; verified create/read/update within root | preflight and Google authorization |
| `deploy_application` | application host, immutable release, database app ref, schema state, Drive access, API token ref | `Deployment`: release ID, fingerprint, candidate endpoint, verification evidence; not yet activated | host, schema, Drive |
| `start_application` | verified deployment | `ServiceState`: accepting authenticated traffic at stable endpoint | deployment; check external persistence and unauthorized rejection |
| `stop_application` | existing application host | `ServiceState`: production unavailable, data retained | runner stopped/drained when managing both |
| `provision_runner_host` | host spec with `execution_model=process` and durable storage requirements | runner `HostTarget` on a process/container host | preflight and authorization |
| `deploy_runner` | runner host, immutable release, active application's stable endpoint/token; LLM/browser and optional email/calendar capabilities | runner `Deployment`, durable checkpoint location, triggers configured disabled | runner host, active application |
| `start_runner` | runner deployment, active application, enabled stage set | `ServiceState`: triggers/consumption enabled, heartbeat/claim verified | application ready, runner deployment |
| `stop_runner` | existing runner host, drain deadline | `ServiceState`: no new claims, active work drained/checkpointed | existing binding; never provision on stop |

Gmail/Calendar credentials, when those stages are enabled, belong to the
runner's external-system adapters and require separate consent. The runner needs
more than an API URL/token for LLM/browser operations; it still gets no database
or artifact-store credentials. Profiles/resume uploads, journal initialization
and all business-state writes go through the application service after it starts,
before stage consumption. Provisioning may create infrastructure/folder ACLs;
it must not import applications or manipulate stage queues directly.

The `up` plan is host → database → schema → Drive → application release →
application activation → runner host → runner release → runner activation.
Independent provision steps can be reordered later; the initial orchestrator is
sequential. `start` uses saved deployments; `stop` drains runner then stops API.
Neither start nor stop creates missing resources. A missing deployment is a
precondition error. Updating a running installation first quiesces the runner;
use backward-compatible schema migrations before promoting a release.

## Partial failure and idempotency contract

1. Preflight the **entire selected plan** before any mutation: provider/plan
   compatibility, identity, credentials, source artifacts, migration checksums,
   quotas, capability support and target ownership. An interactive consent step
   yields `blocked` with a concrete continuation; it never masquerades as ready.
2. Acquire one installation/environment lock. A local file lock covers one host
   only; multiple provisioners require a shared lease with fencing or must be
   explicitly disallowed. The live lifecycle holds an installation file lock. Never race two discovery-then-create operations.
3. Checkpoint intent and stable operation key **before** a remote write; save
   returned resource IDs and secret references atomically after it. Keys are
   derived from installation, environment, task and spec digest, not timestamps.
4. On every rerun observe remote state, even for completed tasks. Reconcile by
   recorded IDs, ownership markers and exact names within the selected account.
   Multiple matches, a different owner, or mismatched existing bindings stop the
   run. Do not adopt arbitrary prefix matches or rotate passwords on retries.
5. A timeout after a write has an **unknown outcome**. Discover/poll the same
   operation first. Do not retry a non-idempotent create until absence is proved;
   if it cannot be proved, require reconciliation. Tag releases with the desired
   fingerprint and inspect pending/failed releases before creating another.
6. Preallocate and persist a Drive folder ID (supported for folders), then create
   with that ID and an installation marker. On 409, retrieve and validate it.
   Drive names are not unique. Permission grants reconcile principal/type/role
   and inherited access. Do not create a new service-account key each run;
   prefer federation where validated. Native Docs creation cannot reuse a
   pre-generated file ID, so journal initialization needs its own marker,
   serialized creation and unknown-outcome recovery through the application API.
7. PostgreSQL database creation is outside a transaction under a session lock;
   role/grant setup and schema migrations use appropriate transactions/locks.
   Re-read catalogs after reconnecting. Record migration checksum atomically with
   DDL. A failed DDL transaction rolls back; never automatically drop a schema.
8. Keep successful resources on downstream failure and stop dependent tasks.
   Reconcile/clean temporary bootstrap credential bindings in `finally` and on
   the next run. Do not delete databases, folders, or prior releases as rollback.
   A failed candidate stays unpromoted; the previous release remains available.
9. If promotion/start times out, inspect deployment/availability before retry.
   Stop first prevents new runner claims and allows a bounded drain. In-flight
   job submissions with unknown outcomes remain for manual reconciliation, not
   automatic replay after restart. A drain timeout is not `stopped` success.
10. Expose `changed`, `unchanged`, `blocked`, `unsupported`, `failed`, and
    `unknown` outcomes with sanitized evidence and resume guidance. A no-op must
    name the already-satisfied postcondition. Plans/logs/reports exclude secrets;
    private state is 0700/0600, atomic, excluded from source and deployment builds.

Deployment fingerprints include artifact digest, configuration, secret **versions**
and provider settings. Repeating an identical deploy must not create a second
deployment. Changed desired inputs may legitimately create a new release without
duplicating the logical installation. Provisioning success, schema readiness,
application readiness, and runner readiness are separate claims.

## Integration sequence and acceptance

1. Define this lifecycle and typed hooks in the main project. Bring the initial
   DDL into `sql/schema.sql`, without moving provider code or private state yet.
2. Split and import the bootstrap adapters under the new namespace; remove
   bootstrap deployment from host provisioning and secret publication from DB
   provisioning. Preserve source attribution and add explicit legacy state import.
3. Implement the selected Google mode and runtime prerequisites. Add API-owned
   PostgreSQL persistence/migrations and journal initialization. The three SQL
   tables are not a complete durable cloud queue/lease/idempotency store.
4. Add application deployment/authentication/persistence probes, then runner
   packaging, deployment, activation and drain. Make onboarding select the setup
   before profile questions once these hooks actually work.
5. For each implemented profile capture remote IDs/catalogs before run A, after A,
   and after identical run B. Compare project, DB resource/database/role/table,
   folder/permission/principal/key, release, trigger and durable-volume identities.
   Verify unchanged passwords/API tokens privately. B must add no resources and
   preserve persisted data; a printed "already exists" is insufficient evidence.
6. Also test lost responses, termination between remote write/checkpoint, missing
   local state, ambiguous resources, revoked credentials, schema failure, failed
   deployment, and overlapping runners. Sequential rerun success alone does not
   establish crash recovery or concurrent safety.

Current live checks cover schema/role verification, Drive read/update access,
production API readiness/authentication, REST/MCP reads, and Docker runner
image/container/volume deployment. Dedicated
Drive file-creation consent remains a separate gate. Record observed results in a readable PDF, with private
machine checkpoints kept outside version control. Do not start job submission
stages merely to test infrastructure idempotency.


## Primary sources

- [Docker Compose create](https://docs.docker.com/reference/cli/docker/compose/create/) and [up](https://docs.docker.com/reference/cli/docker/compose/up/)
- [Google Shared Drives and service-account storage](https://developers.google.com/workspace/drive/api/guides/about-shareddrives)
- [Google service-account creation prerequisites](https://docs.cloud.google.com/iam/docs/service-accounts-create)
- [Google Drive OAuth scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
- [Google desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app) and [token expiration](https://developers.google.com/identity/protocols/oauth2)
- [Drive permission inheritance](https://developers.google.com/workspace/drive/api/guides/manage-sharing) and [pre-generated IDs](https://developers.google.com/workspace/drive/api/guides/manage-uploads#pre-generated-ids)
