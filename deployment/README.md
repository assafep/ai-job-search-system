# Provision and deployment

One entry point with separate deployment and activation commands:

1. **Provision** — make resources exist: a host, a database, a Drive folder.
2. **Deploy** — install one release onto a host that already exists.
3. **Start** — activate the deployed Docker stage runner with `start`.

`up` provisions and deploys the API and a stopped runner. `start`, `stop`, and `status` operate on that existing Docker runner; they do not provision resources or pause the Application Service.

```text
./deployment/ctl.sh
        │
        ├─ plan     show the steps, change nothing
        ├─ drive    bind an existing My Drive folder as the AJSS root
        ├─ drive-login    authenticate with Google Drive
        ├─ up       provision host, database, Drive, applicant, deploy API, verify MCP
        ├─ start    check API readiness and activate the deployed Docker runner
        ├─ stop     finish current work and stop the runner
        ├─ status   inspect runner state and readiness
        ├─ connect  deploy the API, verify MCP, and register Codex
        ├─ chatgpt  verify REST/MCP reads and regenerate private connection files
        ├─ codex    register the local stdio launcher in the host's Codex CLI
        └─ mcp-stdio    start the stdio transport for an MCP client
```

## Commands

Inspect the contract:

The answers to the setup questions are in [defaults.json](defaults.json): which setup, which action, the Drive mode, and the Drive folder path. Edit that file instead of passing them on the command line. A flag still overrides one value for a single run.

```bash
./deployment/ctl.sh plan
./deployment/ctl.sh plan --setup local-docker
```

Authenticate with Google Drive separately, without running the full `up` pipeline:

```bash
./deployment/ctl.sh drive-login
```

First save the downloaded Desktop OAuth client JSON as `.onboarding/oauth-client.json`. If it is missing, the script prints the exact expected path and setup instructions; consent cannot begin yet. Once the file exists, the script prints the actual Google consent URL and asks the host to open it in your browser. If automatic opening fails, open that printed URL on the same computer. Your existing Google session can be used to approve AJSS access. `drive.file` names the permission; it is not a sign-in URL. The script saves refresh credentials and reuses them on later runs.

Bind the folder from `drive_path` and point `config/runtime.json` at it:

```bash
./deployment/ctl.sh drive
```

The only prompt is a browser login when the current `gcloud` account cannot see Drive. The folder must already exist. A second run reuses the same Cloud project, service account, key, and writer grant.

Provision the Vercel project and Neon database, bind Drive, prepare the applicant profile, deploy the Application Service, and verify the MCP connection:

```bash
./deployment/ctl.sh up
```

`up` owns the complete sequence. If `config/runtime.json` is missing, an
interactive run starts the onboarding wizard; non-interactive execution stops
before calling providers and prints `./deployment/ctl.sh onboard`. Run that
command once to supply the required first-run inputs.

Every `up` invocation reconciles every stage against its current resources.
`.onboarding/up-stages.json` records progress for diagnostics and never silently
skips validation. An absent credential or required consent produces a concrete
blocked result; dependent stages do not run. Each invocation writes a readable
PDF to `.onboarding/provisioning-report.pdf`. No agent-side Python snippets,
sibling checkout, manually copied checkpoints, or hidden recovery commands are
part of the supported flow.

A Drive project marked for deletion is reported explicitly; `up` does not
silently resurrect deleted infrastructure. The operator can run
`./deployment/ctl.sh restore-drive-project` to restore only the AJSS project
derived from the configured folder, then rerun `up`. The recovery command checks
the project identity, waits for ACTIVE, and is safe to repeat.

`up` first validates/configures the runtime, then runs these steps in order: `provision_application_host` (Vercel project and Neon database via the local Vercel/Neon adapter), `provision_drive` (bind the Drive folder), `prepare_applicant` (extract and verify the résumé profile), `deploy_application` (publish the Python Application Service), `prepare_chatgpt_connection` (verify REST and MCP reads, write private connection files), and `provision_runner_host` (Docker image, persistent volume, and stopped runner container). The production URL is determined from the Vercel project domain and saved in the deployment checkpoint. `/health` and `/ready` are public probes. REST state routes require the deployment bearer token. `/mcp` accepts OAuth or that bearer token; OAuth discovery and authorization routes provide their own protocol authentication.

The provisioning image includes Codex for résumé extraction. An existing host Codex login is mounted at runtime for `up`. When no host login
is available, use `./deployment/ctl.sh codex-login`; that fallback login is saved
under `.onboarding/codex/`. The host Grok binary and login are also available
during `up`, and configured provider/model overrides are honored. An already
verified résumé/profile is reused.

Python 3.10 or newer is required for the MCP adapter (the provisioning image uses Python 3.12). Set `ONBOARD_PYTHON` when `python3` is older.

## MCP connections

The production MCP URL is the deployed service URL with `/mcp` appended, using stateless
Streamable HTTP. Configure remote clients with that URL and OAuth. The client
uses dynamic registration, and the owner authorizes on the AJSS consent page
with the existing service token. Header-based clients can instead send the
service bearer token directly. See the [endpoint and authentication reference](../application_service/README.md#mcp-endpoint).

```bash
./deployment/ctl.sh connect    # Deploy the API, verify MCP, and register Codex
./deployment/ctl.sh chatgpt    # Verify REST/MCP reads and regenerate private connection files
./deployment/ctl.sh codex      # Register the local stdio launcher in the host's Codex CLI
./deployment/ctl.sh mcp-stdio  # Start the stdio transport for an MCP client
```

`up` prepares the connection after deployment. `up`, `connect`, and `chatgpt` also attempt
local registration when they produce a fresh verified handoff and `codex` is
available on the host. The generated files live in `.onboarding/chatgpt/`;
see [file roles and connection steps](../provisioning/README.md#chatgpt-and-codex-connection-handoff).
Credentials stay in private files and are omitted from stage output.

The configured Docker runner is deployed automatically by `up`; inspect or retry
that stage separately with `./deployment/ctl.sh runner`. It includes the pinned
Codex CLI and the installed host Grok binary, using read-only build-time binary
injection. Existing host CLI logins are mounted at runtime; they never enter an
image. `AGENT_PROVIDER`, `CODING_AGENT`, and model overrides pass through the
launcher. The image and stopped container are reused when their configuration
is unchanged. Running containers are never replaced automatically. Persistent
workload/approval state lives in an installation-specific Docker volume. Queue
activation remains a separate operation; native ChatGPT website stages do
not execute in this container.

For OAuth, the server needs database connectivity, a service token, and
`AJSS_MCP_ISSUER` set to the canonical HTTPS origin without `/mcp`. Deployment
sets the issuer from its saved production URL. Preserve the database-backed
OAuth records across releases. If the service token rotates, reconnect OAuth
clients. Regenerate the handoff after URL, token, or API changes.

MCP's `stages` profile exposes stage requests and related reads; the
`domain` profile also exposes direct domain mutations. MCP does not provision or
start workers. See the [native-browser protocol and remaining-stage host setup](chatgpt-stages.md) for a
supervised local consumer. Docker runner deployment is separate from activation; it creates a stopped container
and never starts job processing during provisioning.

## Where the code lives

```text
deployment/ctl.sh         the command
deployment/defaults.json   answers to the setup questions
deployment/lifecycle.md        the full task contract and recovery rules
deployment/vercel/             Python API package published by deploy_application
deployment/docker/             Application Service image
deployment/kubernetes/         long-running API manifest
deployment/cloud-run/          Cloud Run notes
deployment/systemd/            worker service template for supervised workstation

provisioning/lifecycle.py      the task list behind plan
provisioning/execute.py        what up runs today
provisioning/drive_folder.py   what drive runs
provisioning/chat_worker.py    installs the systemd user service for the ChatGPT worker
provisioning/bootstrap/        imported Vercel and Neon adapter
```

`provisioning/bootstrap` contains repository-owned Vercel/Neon adapters. All checkpoints stay in this repository’s `.onboarding/`; no sibling checkout is required. `deploy_application` uploads `deployment/vercel/` plus the Application Service package directly to Vercel.

## What is live

| Step | Status |
| --- | --- |
| `plan` | Read-only. Shows the task graph and implementation gaps. |
| `drive` | Live. Binds an existing Drive folder and updates `config/runtime.json`. |
| `drive-login` | Live. Opens browser OAuth consent for Google Drive. |
| `up` — provision host & database | Live via the local Vercel/Neon adapter. Idempotent on the existing Vercel project and Neon database. |
| `up` — bind Drive | Live. StageTasks `provision_drive` step. |
| `up` — prepare applicant | Live. Extracts profile from résumé and verifies `Applicant/applicant_profile.json`. |
| `up` — deploy application | Live. Uploads the Python Application Service to Vercel when source or settings change. |
| `up` — prepare MCP connection | Live. Verifies REST/MCP reads and writes private connection files; remote client setup remains manual. |
| `up` — provision runner host | Builds an image with Codex/Grok and creates a persistent volume and stopped runner container. |
| `connect` | Live. StageTasks deploy, MCP verification, and Codex registration. |
| `chatgpt` | Live. Verifies REST/MCP reads and regenerates private connection files. |
| `codex` | Live. Registers the local stdio launcher in the host's Codex CLI. |
| `mcp-stdio` | Live. Starts the stdio transport for an MCP client. |
| Supervised workstation ChatGPT worker | Available through `python3 -m provisioning.chat_worker`; installs a systemd user service from `deployment/systemd/` and requires configured local agent/browser tools. |
| `start` / `stop` / `status` | Activate, gracefully drain, or inspect the existing Docker runner. |

```bash
./deployment/ctl.sh start
./deployment/ctl.sh status
./deployment/ctl.sh stop --timeout 120
```

`start` checks the deployed runner's ownership, API readiness, authenticated queue reads, and the stage API is reachable before activating the container. It waits for worker readiness. Repeating `start` rechecks readiness without starting a second consumer. After updating to this version, run `up` once to deploy the runner's graceful lifecycle support.

The worker polls scheduled and application-state queues at their declared cadence and consumes pending workloads. Gmail/Calendar external events still need their separately configured adapters. `validate-bookmarked` and `submit-prepared` remain on the native ChatGPT host and are never claimed by this CLI worker. A shared file lock prevents another local registry writer from running alongside this installation's Docker executor. Interrupted running workloads block restart until manually reconciled.

`stop` prevents new workload claims and finishes the current attempt. Its default wait is 60 seconds (`--timeout 1..3600`). A timeout returns failure with a draining message and leaves the process alive; it never sends SIGKILL or replays an uncertain submission. Repeat `status` or `stop` to observe completion. Stopping an already stopped runner is a no-op. The runner has no automatic restart policy. The API, database, Drive files, and persisted worker registries remain available.

## Other host shapes

Docker, Kubernetes, and Cloud Run can host the Application Service as a long-lived process. Vercel hosts its HTTP API as functions. This source does not provision managed stage consumers there. Validation and submission website interaction belongs to ChatGPT native browser; other stages use the separately activated workstation worker. The worker path is:

Adapter events enter the workload registry; `services.stage_executor` executes
the stage through the Application Service.

```bash
python3 -m services.worker_main --poll-seconds 30
```

Keep Google credentials and `APPLICATION_SERVICE_TOKEN` in the host's secret manager. Worker registries (`WORKLOAD_REGISTRY_PATH`, and `APPROVAL_REGISTRY_PATH` when approvals are on) need durable storage. They hold scheduler and approval bookkeeping. Application state stays in the Application Service. For a cloud worker, set `AGENT_PROVIDER`. The default provider expects a local Codex login.

## Run stages from ChatGPT

See [ChatGPT stage execution](chatgpt-stages.md) for the MCP stage tools,
durable background worker, scoped requests, PDF reports, and reconciliation.

## Applicant onboarding dependency

Immediately after `provision_drive`, `up` runs `prepare_applicant`. It ensures
`Applications` and `Applicant`, waits for a readable résumé PDF in Applicant,
extracts the profile and verifies `Applicant/applicant_profile.json` before
deploying. For an existing installation, run
`python3 -m services.onboarding --applicant`. See [the stage diagram and setup](../docs/onboarding.md).
