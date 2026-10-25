# Workflow execution architecture

## Current implementation

ChatGPT starts and monitors workflows through MCP. Python workflow execution
runs on the configured workstation worker. The same runtime can also be hosted
on a cloud process/container host. A CLI or continuous scheduler is another
entry point; choose one registry-writing runner for an installation.

```mermaid
flowchart TB
    USER[User] --> CHAT[ChatGPT]
    CHAT --> MCP["Application Service MCP endpoint<br/>OAuth or bearer authentication<br/>Workflow tools and related reads"]

    subgraph VERCEL["Vercel — Application Service"]
        MCP --> RUNAPI["Workflow request API<br/>Validate scope and idempotency<br/>Snapshot eligible application IDs"]
        REST["Authenticated REST API<br/>OpenAPI validation"]
        CLAIM["Worker claims, heartbeats and completion<br/>Uncertain runs require reconciliation"]
        DOMAIN["Application operations<br/>Validated lifecycle transitions<br/>Events, artifacts and file storage"]
        BULK["Bulk update API<br/>Up to 25 typed items per request<br/>Independent results and durable retry receipts"]
        REPORT["Run status and readable PDF retrieval"]
        REST --> CLAIM
        REST --> DOMAIN
        REST --> BULK
        BULK --> DOMAIN
        REPORT --> MCP
    end

    subgraph SQL[PostgreSQL]
        REQUESTS[("Durable workflow-request queue<br/>Queued / running / completed / uncertain")]
        TRACKING[("Run tracking and idempotency<br/>OAuth records<br/>Completed application snapshots")]
        FILES[("Workflow-owned files<br/>Checkpoints and submission receipts<br/>Bulk mutation receipts and PDF reports")]
    end

    RUNAPI --> REQUESTS
    RUNAPI --> TRACKING
    CLAIM <--> REQUESTS
    CLAIM --> TRACKING
    DOMAIN <--> TRACKING
    DOMAIN <--> FILES
    REPORT --> TRACKING
    REPORT --> FILES

    subgraph HOST["Selected Python execution host — workstation or cloud"]
        WORKER["Supervised ChatGPT request worker<br/>Consumes explicit workflow requests"]
        CLI[CLI / automatic scheduler]
        LOCK["Exclusive runner lock<br/>Shared persistent workload and approval registries"]
        RUNTIME["Existing Python workflow runtime<br/>Queue, scheduled and external-event workflows"]
        POOL["Up to eight posting workers<br/>Synchronized Application Service client"]
        CONTEXT[Gather serialized application context]
        AGENTS["Stateless agents<br/>Structured decisions"]
        VALIDATE["Validate decisions and enforce policy<br/>Prepared membership authorizes auto-submit"]
        ADAPTERS["Runtime-owned adapters<br/>Posting retrieval / browser submission<br/>Email / Calendar"]
        WORKER --> LOCK
        CLI --> LOCK
        LOCK --> RUNTIME
        RUNTIME --> POOL
        POOL --> CONTEXT --> AGENTS --> VALIDATE
        VALIDATE --> ADAPTERS
    end

    WORKER <-->|Claim and report over HTTPS| REST
    CONTEXT -->|Read context| REST
    VALIDATE -->|Explicit state writes| REST
    RUNTIME -->|Workflows 8, 9 and 10 item writes| BULK

    subgraph DRIVE[Google Drive / Google Docs]
        JOURNAL[("Application journal and lifecycle queues<br/>Discovered / Bookmarked / Validated / Prepared<br/>Applied / Interviewing / terminal and manual states")]
        ARTIFACTS[("Applicant profile and application packages<br/>Resume / cover letter / research")]
        LEGACY[(Legacy workflow files remain readable)]
    end

    DOMAIN <--> JOURNAL
    DOMAIN <--> ARTIFACTS
    DOMAIN --> LEGACY
    ADAPTERS <--> EXTERNAL[Job boards / employer sites / Gmail / Calendar]
```

The workflow-request queue in PostgreSQL holds requests to execute work. The
application queues in the journal describe posting lifecycle states. The host's
workload and approval registries track execution bookkeeping. These are distinct
responsibilities.

The workflow-only MCP profile exposes execution requests and relevant reads.
The domain profile additionally exposes direct domain operations, including
`bulkUpdateItems`. Internal claim and worker-update endpoints are excluded from
MCP. Agents receive serialized context and return decisions; runtimes own tool
calls and adapter execution. Application state changes belong to the Application
Service.

Bulk updates validate the whole request schema and return per-item outcomes;
they are not a distributed all-or-nothing transaction. Durable intents prevent
blind retries of uncertain mutations. Workflows 8 and 10 batch completion and
removal operations; workflow 9 uses bulk grant installation and acknowledgement.

Automatic `prepare-interview` polling defaults to 86400 seconds, configurable
through `pipelines.interview.event_poll_seconds`. It polls at scheduler startup
and every 24 hours afterward; restarting the scheduler resets that timer.
Explicit CLI and ChatGPT requests remain on demand. The request worker itself
does not activate a continuous automatic scheduler.

## Proposed execution split

This is a proposed redesign, not the current deployment. Nine workflows would
execute in Vercel, and ChatGPT would own bookmark and auto-submit workflow
sessions. Hosting those nine runtimes in Vercel and implementing the ChatGPT
execution boundary require additional work. MCP supplies tools; it does not
move Python execution into a chat session automatically.

```mermaid
flowchart TB
    USER[User] --> CHAT

    subgraph CHAT["ChatGPT-controlled workflow sessions — proposed"]
        BOOKMARK["1. Bookmark discovered positions<br/>Read postings and produce bookmark decisions"]
        SUBMIT["4. Auto-submit Prepared applications<br/>Coordinate submission and evaluate evidence"]
        TOOLS[Context and execution tool calls]
        BOOKMARK --> TOOLS
        SUBMIT --> TOOLS
    end

    subgraph VERCEL["Vercel — proposed runtime split"]
        MCP[Authenticated MCP endpoint]
        DISPATCH["Durable dispatcher<br/>Route by workflow ownership<br/>Exclusive claims and idempotency"]
        SCHEDULE[Scheduled / polling entry points]
        DISCOVER[0. Discover positions]
        VALIDATE[2. Validate bookmarked positions]
        PREPARE[3. Prepare application packages]
        MAIL[5. Handle incoming email]
        INTERVIEW[6. Prepare interviews — daily default]
        RECONCILE[7. Reconcile]
        COMPLETE[8. Complete journal]
        GRANT[9. Renew Gmail grant]
        GHOST[10. Identify ghosting]
        RUNTIME["Shared Python workflow runtime<br/>Stateless agents and validated decisions"]
        BOUNDARY["ChatGPT execution boundary<br/>Validate current state and decisions<br/>Invoke runtime-owned adapters"]
        API["Application Service API<br/>Single and bulk domain operations<br/>Status and PDF reports"]

        MCP --> DISPATCH
        MCP --> BOUNDARY
        DISPATCH --> SCHEDULE
        SCHEDULE --> DISCOVER & VALIDATE & PREPARE & MAIL & INTERVIEW
        SCHEDULE --> RECONCILE & COMPLETE & GRANT & GHOST
        DISCOVER & VALIDATE & PREPARE & MAIL & INTERVIEW --> RUNTIME
        RECONCILE & COMPLETE & GRANT & GHOST --> RUNTIME
        RUNTIME --> API
        BOUNDARY --> API
    end

    TOOLS <-->|MCP requests and results| MCP
    DISPATCH -.->|Bookmark work| BOOKMARK
    DISPATCH -.->|Prepared work authorizes submission| SUBMIT

    DB[("PostgreSQL<br/>Workflow requests, claims, idempotency<br/>Checkpoints, receipts and PDF reports")]
    DRIVE[("Google Drive / Docs<br/>Application lifecycle queues<br/>Profile, packages and research")]
    ADAPTERS["Runtime-owned external adapters<br/>Browser / posting retrieval / email / Calendar"]
    EXTERNAL[Job boards / employer sites / Gmail / Calendar]

    DISPATCH <--> DB
    API <--> DB
    API <--> DRIVE
    BOUNDARY --> ADAPTERS
    RUNTIME --> ADAPTERS
    ADAPTERS <--> EXTERNAL
```

In that proposed split, the main pipeline crosses execution environments:

```mermaid
flowchart LR
    DISCOVER[Discover — Vercel] --> Q1[(Discovered)]
    Q1 --> BOOKMARK[Bookmark — ChatGPT]
    BOOKMARK --> Q2[(Bookmarked)]
    Q2 --> VALIDATE[Validate — Vercel]
    VALIDATE --> Q3[(Validated)]
    Q3 --> PREPARE[Prepare package — Vercel]
    PREPARE --> Q4[(Prepared)]
    Q4 --> SUBMIT[Auto-submit — ChatGPT]
    SUBMIT --> Q5[(Applied)]
```

Browser actions still need an execution adapter, and persistent writes still
pass through the Application Service. ChatGPT owns the two workflow sessions;
MCP connects those sessions to the execution capabilities.
