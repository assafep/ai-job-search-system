# Provision and deployment

One command, three ideas.

| Word | Meaning |
| --- | --- |
| Provision | Make a resource exist: a host, a database, a Drive folder. |
| Deploy | Install one release onto a host that already exists. |
| Start | Let that release serve traffic or take work. |

```text
./deployment/ctl.sh
        │
        ├─ plan     show the steps, change nothing
        ├─ drive    bind an existing My Drive folder as the AJSS root
        └─ up       provision the Vercel host and database, bind Drive, deploy the Application Service
```

## Commands

Inspect the contract:

The answers to the setup questions are in [defaults.json](defaults.json): which profile, which action, the Drive mode, and the Drive folder path. Edit that file instead of passing them on the command line. A flag still overrides one value for a single run.

```bash
./deployment/ctl.sh plan
./deployment/ctl.sh plan --setup local-docker
```

Bind the folder from `drive_path` and point `config/runtime.json` at it:

```bash
./deployment/ctl.sh drive
```

The only prompt is a browser login when the current `gcloud` account cannot see Drive. The folder must already exist. A second run reuses the same Cloud project, service account, key, and writer grant.

Provision the Vercel project and Neon database, then bind Drive:

```bash
./deployment/ctl.sh up
```

`up` records each successful stage in `.onboarding/up-stages.json`. If a later stage fails, the next run offers to continue from that stage. Answering `n` starts over.

`up` calls the sibling project `auto-signup-vercel` to create or reuse the Vercel project `ajss` and its Neon database. `deploy_application` then publishes the Python Application Service to that project. The production URL is `https://ajss-one.vercel.app`. `/health` and `/ready` are public probes. REST state routes require the deployment
bearer token. `/mcp` accepts OAuth or that bearer token; OAuth discovery and
authorization routes provide their own protocol authentication.

Python 3.10 or newer is required for the MCP adapter (the provisioning image uses Python 3.12). Set `ONBOARD_PYTHON` when `python3` is older.

## MCP connections

The production MCP URL is **`https://ajss-one.vercel.app/mcp`**, using stateless
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

For OAuth, the server needs database connectivity, a service token, and
`AJSS_MCP_ISSUER` set to the canonical HTTPS origin without `/mcp`. Deployment
sets the issuer from its saved production URL. Preserve the database-backed
OAuth records across releases. If the service token rotates, reconnect OAuth
clients. Regenerate the handoff after URL, token, or API changes.

MCP's `workflows` profile exposes workflow requests and related reads; the
`domain` profile also exposes direct domain mutations. MCP does not provision or
start workers. Use the [workstation worker setup](chatgpt-workflows.md) for a
supervised local consumer. Docker runner provisioning can remain blocked after
a successful API deployment and connection verification.

## Where the code lives

```text
deployment/ctl.sh         the command
deployment/defaults.json   answers to the setup questions
deployment/lifecycle.md        the full task contract and recovery rules
deployment/vercel/             Python API package published by deploy_application
deployment/docker/             Application Service image
deployment/kubernetes/         long-running API manifest
deployment/cloud-run/          Cloud Run notes

provisioning/lifecycle.py      the task list behind plan
provisioning/execute.py        what up runs today
provisioning/drive_folder.py   what drive runs
provisioning/bootstrap/        imported Vercel and Neon adapter
```

Do not run `provisioning/bootstrap` by itself. Its paths still belong to the sibling project. `deploy_application` uploads `deployment/vercel/` plus the Application Service package over the bootstrap probe.

## What is live

| Step | Status |
| --- | --- |
| `plan` | Read-only. |
| Vercel project, Neon database, schema | Live through the sibling bootstrap. Idempotent on the existing project. |
| Drive folder binding | Live through `drive`. |
| Deploy the Application Service | Wired. `up` uploads the Python service to the existing Vercel project when the source or public settings change. |
| Prepare MCP connection handoff | Wired. Verifies REST/MCP reads and writes private connection files; remote client setup remains manual. |
| Deploy and start the Docker workflow runner | Not wired. |
| Supervised workstation ChatGPT worker | Available through `.venv/bin/python -m provisioning.chat_worker`; requires configured local agent/browser tools. |

`start` and `stop` are in the contract. They are not executable yet. Neither creates missing resources.

## Other host shapes

Docker, Kubernetes, and Cloud Run run the Application Service as a long-lived process. The Vercel profile serves that API as functions and runs workflows on a separate process host. The worker path is:

```text
adapter event → workload registry → services.workflow_runtime → Application Service
```

```bash
python3 -m services.worker_main --poll-seconds 30
```

Keep Google credentials and `APPLICATION_SERVICE_TOKEN` in the host's secret manager. Worker registries (`WORKLOAD_REGISTRY_PATH`, and `APPROVAL_REGISTRY_PATH` when approvals are on) need durable storage. They hold scheduler and approval bookkeeping. Application state stays in the Application Service. For a cloud worker, set `AGENT_PROVIDER`. The default provider expects a local Codex login.

## Run workflows from ChatGPT

See [ChatGPT workflow execution](chatgpt-workflows.md) for the MCP workflow tools,
durable background worker, scoped requests, PDF reports, and reconciliation.
