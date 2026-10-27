# Provisioning package

> **Legacy provisioning/compatibility:** the `up`, stage-task runner and connection-generation flows below belong to the old Application Service. They remain useful for Google OAuth, profile onboarding and API clients, but do **not** install, migrate or launch GDTQ. See [GDTQ](../gdtq/README.md).

---

## Historical / compatibility reference


This package is the implementation behind [deployment/ctl.sh](../deployment/ctl.sh). The operator guide is [deployment/README.md](../deployment/README.md). The task contract is [deployment/lifecycle.md](../deployment/lifecycle.md).

| Module | Role |
| --- | --- |
| `lifecycle.py` | Names the steps for `plan`. |
| `runner_control.py` | Starts, gracefully drains, and inspects the deployed Docker runner. |
| `execute.py` | StageTasks the steps that exist for `up`. |
| `drive_folder.py` | Binds an existing Drive folder for `drive`. |
| `drive_storage.py` | Drive, Cloud project, and service-account calls. |
| `bootstrap/` | Repository-owned Vercel and Neon adapters used by the live provisioning hooks. |
| `contracts.py` | Shared result types. |

## ChatGPT and Codex connection handoff

The deployed Application Service provides a working MCP endpoint at
`https://ajss-one.vercel.app/mcp` (or `<service-origin>/mcp` for another installation).
Its transport, authentication, and tool contract are documented in the
[Application Service guide](../application_service/README.md#mcp-endpoint).

`./deployment/ctl.sh up` runs `prepare_chatgpt_connection` immediately after
`deploy_application`, before runner provisioning. The stage verifies an
authenticated REST queue read, performs a real MCP initialization and tool listing,
and calls the read-only `journalQueueCounts` tool. Verification does not run
stages or mutate applications. `./deployment/ctl.sh chatgpt` regenerates this
handoff independently after a URL, token, or API change; it requires an
application-ready deployment checkpoint and does not provision or deploy anything.

The stage writes `.onboarding/chatgpt/` with directory mode 0700 and file mode 0600:

| File | Purpose |
| --- | --- |
| `connection.json` | Remote `/mcp` URL, OAuth authentication choice, and credential-file reference. |
| `mcp.json` | Local stdio launcher configuration, with no embedded credential. |
| `SETUP.txt` | Connection instructions for this deployment. |
| `verified.json` | Verification timestamp, MCP URL, and discovered tool count. |
| `openapi.yaml`, `instructions.txt` | Optional GPT Action fallback and persistence-boundary instructions. |
| `authentication.json` | Private service bearer token; never commit or publish it. |

For a remote MCP connection, use the URL in `connection.json`, select OAuth,
and allow dynamic client registration without supplying preconfigured client
credentials. Complete the AJSS consent page with the existing service token,
then test `journalQueueCounts`. Connection setup in the remote client remains a
one-time user action; preparing files does not mean that client is connected.

For an existing installation, deploy the updated Application Service and prepare
the connection without rerunning host, database, Drive, or runner provisioning:

```bash
./deployment/ctl.sh connect
```

This also attempts Codex registration on the host after a successful verification.

For local host registration, run:

```bash
./deployment/ctl.sh codex
```

This registers `ajss-application-service` with the host's Codex CLI using
`deployment/ctl.sh mcp-stdio`. The launcher reads saved deployment credentials
at startup. After `up`, `connect`, or `chatgpt`, the wrapper also attempts host registration
when the connection stage wrote a fresh verification file and `codex` is installed,
even if the later runner-provisioning stage is blocked. Registration changes host
configuration; it does not configure a remote client or start stage workers.

The GPT Action fallback uses the generated OpenAPI schema with API key / Bearer
authentication and the private token in `authentication.json`. The REST base URL
belongs in that schema; the `/mcp` URL belongs in an MCP client configuration.
