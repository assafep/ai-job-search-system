# Runtime communication — concurrency view

An experimental simplification of the [original architecture diagram](architecture.md#process-boundaries-and-network-communication).
Within our system, boxes represent execution hosts or independently running work.
The repeated worker boxes represent a pool; **N is configuration-dependent**, not
three fixed workers. Adapters, agent runners, executors and HTTP clients are
folded into the execution lanes that call them.

```mermaid
flowchart LR
    CHATGPT["ChatGPT host<br/>Stage orchestration + native Browser / Computer Use<br/>validate-bookmarked / submit-prepared"]

    subgraph WORKFLOW["Workstation workflow process"]
        MAIN["Coordinator · main thread<br/>Poll / claim, dispatch, collect results<br/>Context reads and persistence operations"]
        HEARTBEAT["Heartbeat thread<br/>Renew active task progress"]
        subgraph POOL["Concurrent work · worker pool × N"]
            W1["Worker 1<br/>Stage work + adapters / agent invocation"]
            W2["Worker 2<br/>Stage work + adapters / agent invocation"]
            WN["⋯ Worker N<br/>Stage work + adapters / agent invocation"]
        end
        MAIN -.->|"Dispatch / collect"| W1
        MAIN -.-> W2
        MAIN -.-> WN
        MAIN -.->|"Start / stop with active task"| HEARTBEAT
    end

    APP["Application Service<br/>REST + MCP, authentication, validation<br/>Claims, state mutations, idempotency and persistence"]

    subgraph WWW["WWW / external HTTPS services"]
        SITES["Employer / ATS websites<br/>Posting pages and application forms"]
        PROVIDERS["Job-board / Gmail / Calendar / scheduling APIs<br/>Configured integrations"]
        MODELS["Configured LLM provider API"]
        DOCS["Google Docs API<br/>Active applications, journal events, research tabs"]
        DRIVE["Google Drive API<br/>Profile, résumés, packages, reports, internal files"]
        AUTH["Google OAuth token endpoint"]
    end

    PG[("PostgreSQL<br/>Completed application archives<br/>Stage tasks, claims, installation and OAuth records<br/>Optional workload-owned internal files")]

    CHATGPT -->|"HTTPS · MCP · POST /mcp<br/>Task context, packages, submission intent and results"| APP
    CHATGPT -->|"HTTPS · native browser<br/>Browse, fill, upload, submit"| SITES
    WORKFLOW -->|"HTTP(S) · REST / JSON · bearer token<br/>Task claims / updates, context, state and artifact operations"| APP
    WORKFLOW -->|"HTTPS · configured reads / external actions"| PROVIDERS
    WORKFLOW -->|"HTTPS · posting retrieval / discovery"| SITES
    WORKFLOW -->|"HTTPS · model inference when API provider selected"| MODELS
    APP -->|"HTTPS · read / batchUpdate"| DOCS
    APP -->|"HTTPS · list / download / upload"| DRIVE
    APP -->|"HTTPS · token exchange / refresh"| AUTH
    APP -->|"PostgreSQL wire protocol · psycopg<br/>SQL + advisory locks; TLS per connection configuration"| PG
```

Solid arrows are network requests; responses return over the same connection.
Dotted arrows are local dispatch or lifecycle coordination. Network arrows from
the workstation boundary aggregate calls made by its execution lanes through
the shared synchronized client; that client is not a separate thread or service.

The coordinator and worker pool show the concurrency shape, rather than promising
that every stage uses every lane. Sequential stages execute in the coordinator;
parallel-capable execution uses the pool. Heartbeats run while a workstation
stage task is active. The deployment selects its executor; this diagram does not
propose running competing registry writers or the legacy queue utility alongside
the active executor. Native-browser stages remain exclusively in ChatGPT.

An API-backed agent invocation runs within its calling lane. A CLI-backed
invocation can launch a child process, but is folded into the worker because the
caller waits for its result. Agents receive serialized context and return
validated decisions; they have no persistence or external-system clients.
The Application Service's handlers and repositories are likewise collapsed into
one deployment box; its internal request concurrency is outside this view.

Only the Application Service accesses persistence during stage execution.
The workflow polls it for work; there is no service-to-workstation push channel.
Production uses HTTPS; local REST defaults to `http://127.0.0.1:8080`.
