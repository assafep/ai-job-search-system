# Vercel / ChatGPT MCP deployment

The `api/index.py` Python FastAPI service is a stateless remote MCP gateway. It
does not run a permanent scheduler, and **does not automatically submit applications**.

## Deploy configuration

Connect the feature branch to a Vercel project; configure its Python function
entrypoint and root directory. The gateway provides `/api/mcp`,
`/api/health`, and `/api/cron/dispatch`. The root rewrites in `vercel.json`
also expose `/mcp`, `/health`, and `/cron/dispatch`.

Vercel's Python builder ordinarily installs `requirements.txt`; it MUST include
`fastapi`, `pydantic` and `psycopg[binary]` as well as the normal runtime
requirements. Do not rely on a local SQLite filesystem for identity state.

Required secrets:
- `DATABASE_URL`: Neon pooled Postgres URI, configured in the Vercel
  environment's secrets (never in Git).
- `AJSS_MCP_TOKEN`: strong unique bearer token for direct MCP calls.
- `CRON_SECRET`: independent bearer secret for Vercel Cron.

Optional Google access: `AJSS_DOCUMENT_ID`, `AJSS_DRIVE_ROOT_URL`, and
the existing Google OAuth variables. Until these are provided, only Neon tools
are functional. The gateway's write tool and cron processing are **disabled**
unless `AJSS_ENABLE_DISPATCH=1` is explicitly set, after Google Docs live
integration testing and authorization.

MCP: send JSON-RPC 2.0 to POST /api/mcp with `Authorization: Bearer ...`;
supported methods: `initialize`, `ping`, `tools/list`, `tools/call`.
Read tools: `lookup_posting`, `database_status`, `queue_summary`,
`application_events`. Write tool: `dispatch_once` only if enabled.

**Authentication:** direct bearer authentication is for a controlled integration
client. ChatGPT's public remote MCP integration may require OAuth
discovery/authorization flows. This minimal service does **not** implement OAuth
or dynamic client registration, so do not claim it can be connected to
every ChatGPT workspace yet. Configure a trusted OAuth proxy/provider if needed.

No cron schedule is activated by default. Add a Vercel Cron schedule only
after verifying exclusive dispatch locking, function execution time, quotas,
Google OAuth scopes, consent and recovery behavior. The endpoint accepts a
`CRON_SECRET` token and calls a single synchronous dispatch operation.

Never expose identity/database secrets through `/health` or tool errors.
