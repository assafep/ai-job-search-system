# AJSS — GDTQ architecture diagrams

These diagrams describe the **GDTQ-only branch**. They are maintained as Mermaid source and rendered directly by GitHub. The executable source of truth is the code under `gdtq/` and `adapters/`, plus `config/gdtq-routing.json` and `schemas/gdtq/`; diagrams do not define runtime behavior.

## 1. System component architecture

```mermaid
flowchart TB
  timer["1-second scheduler"]
  mailTimer["Hourly mail and calendar observation<br/>optional"]
  reportTimer["Daily report<br/>optional"]
  orch["WorkloadOrchestrator<br/>dispatch and completion"]
  validator["OutcomeValidator"]
  transition["StateTransitionEngine"]
  workers["Processing stage handlers<br/>discovery, validation, preparation, submission"]
  extras["Correspondence / Calendar / Research"]
  queue["GDTQ<br/>pop, claim, push, append, research tabs"]
  archive["CompletionPublisher"]
  retention["RetentionManager"]
  index[("SQLite WorkIndex<br/>identities, claims, checkpoints")]
  doc[("Google Docs<br/>state tabs + application subtabs")]
  drive[("Google Drive<br/>profile, posting, packages, final JSON")]
  ats["ATS / posting API and scraping adapters"]
  llm["AI prompt runner / model provider"]
  gmail["Gmail and Calendar APIs"]

  timer --> orch
  orch --> queue
  queue <--> doc
  orch --> workers
  workers --> ats
  workers --> llm
  workers -->|typed outcome| orch
  orch --> validator
  validator --> transition
  transition -->|accepted next state| orch
  orch --> archive
  archive --> drive
  archive --> queue
  orch --> retention
  retention --> archive
  orch <--> index
  workers --> drive
  mailTimer --> extras
  extras --> gmail
  extras --> orch
  extras --> queue
  reportTimer --> extras
```

**Ownership:** agents and adapters report results; the orchestrator initiates state changes. `OutcomeValidator` and `StateTransitionEngine` are deterministic policy components. `CompletionPublisher` archives and verifies before deleting the active application tab.

## 2. Queue processing and completion sequence

```mermaid
sequenceDiagram
  autonumber
  participant Tick as Scheduler
  participant O as WorkloadOrchestrator
  participant Q as GDTQ
  participant Docs as Google Docs
  participant W as Stage Handler / Agent
  participant V as OutcomeValidator
  participant E as StateTransitionEngine
  participant DB as SQLite WorkIndex
  participant C as CompletionPublisher
  participant D as Google Drive

  Tick->>O: dispatch_tick()
  O->>DB: advance round-robin cursor
  O->>Q: pop(A)
  Q->>Docs: reparent item A → A-onFlight
  Docs-->>Q: same item tab ID / revision
  Q-->>O: Claimed(itemId, lastEvent, attemptId)
  O->>DB: record claim / lease
  O->>W: dispatch item asynchronously
  W-->>O: ProcessingOutcome (enum code + executionStatus)
  O->>V: validate agent-specific schema
  alt Invalid outcome
    V-->>O: validation error
    O->>E: administrative quarantine decision
  else Schema valid
    V-->>O: valid
    O->>E: resolve(A, outcome)
    E-->>O: next state B / terminal flag
  end
  alt Nonterminal
    O->>Q: push(A, B, itemId, event)
    Q->>Docs: append event + reparent A-onFlight → B (one batchUpdate)
  else Terminal
    O->>C: complete(itemId, final state, event)
    C->>Q: application_events(itemId)
    C->>D: write final-event-itemId.json
    C->>D: read back and verify
    C->>Q: delete_application(itemId)
    Q->>Docs: delete source tab
  end
  O->>DB: record event / release claim
```

The in-flight tab is **always owned by the source queue**. No `B-onFlight` hop occurs during `push(A,B,...)`. SQLite, Docs and Drive are separate stores: their changes are not a single distributed transaction, so recovery/reconciliation is required.

## 3. Application states and administrative terminal outcomes

The following diagram emphasizes the primary lifecycle and exceptional routing. For **every** permitted transition, including DLQ redrive and `Invalidated → Bookmarked`, consult `config/gdtq-routing.json`.

```mermaid
stateDiagram-v2
  [*] --> Discovered
  Discovered --> Bookmarked
  Bookmarked --> Validated
  Bookmarked --> Invalidated
  Invalidated --> Bookmarked
  Validated --> Prepared
  Bookmarked --> Prepared
  Prepared --> Applied
  Prepared --> ManualRequired
  ManualRequired --> Applied
  ManualRequired --> Prepared
  Applied --> Interviewing
  Interviewing --> Interviewing: another round
  Applied --> Rejected
  Interviewing --> Rejected
  Applied --> Offer
  Interviewing --> Offer
  Discovered --> Duplicate
  Bookmarked --> Duplicate
  Discovered --> DLQ
  Bookmarked --> DLQ
  Validated --> DLQ
  Prepared --> DLQ
  Applied --> DLQ
  Interviewing --> DLQ
  DLQ --> Discovered: approved redrive
  DLQ --> Bookmarked: approved redrive
  DLQ --> Validated: approved redrive
  DLQ --> Prepared: approved redrive
  DLQ --> Applied: approved redrive
  DLQ --> Interviewing: approved redrive

  state "Manual Apply Required" as ManualRequired
  state "Dead Letters Queue" as DLQ
  state "Failed / Expired / Ghosted" as AdministrativeCompletion

  Discovered --> AdministrativeCompletion: permanent download failure / policy
  ManualRequired --> AdministrativeCompletion: retention
  Applied --> AdministrativeCompletion: ghosted / retention
  Interviewing --> AdministrativeCompletion: retention

  Invalidated --> [*]: archive
  Rejected --> [*]: archive
  Offer --> [*]: archive
  Duplicate --> [*]: archive
  AdministrativeCompletion --> [*]: archive
```

Business-state transitions use the versioned legal graph and outcome mapping. `Failed`, `Expired`, and `Ghosted` are **administrative terminal completions**, not ordinary agent-selected edges. The chart intentionally omits some legal cross-stage shortcuts to remain readable.

## 4. Google Docs tab hierarchy and durable stores

```mermaid
flowchart LR
  subgraph J["Google document — maximum 100 tabs total"]
    direction TB
    A["Discovered"]
    AI["Discovered-onFlight"]
    B["Bookmarked"]
    BI["Bookmarked-onFlight"]
    V["Validated"]
    VI["Validated-onFlight"]
    M["Manual Apply Required"]
    AP["Applied"]
    IT["Interviewing"]
    DLQ["Dead Letters Queue"]
    item["Application tab — stable tab ID"]
    notes["Nested interview research tab"]
    A -->|pop| AI
    AI -->|push to Bookmarked| B
    B --> BI
    BI -->|push| V
    item -.-> notes
  end
  subgraph Storage["Other storage"]
    IDX[("SQLite: posting keys / claims / cursor")]
    Profile["Drive: Applicant/profile and resume"]
    Work["Drive: Applications/company-role/<br/>posting and submission package"]
    Final["Drive: Applications or Failed/<br/>final-event-itemId.json"]
  end
  A -.-> item
  Profile --> Work
  Work --> Final
  IDX -.-> A
```

The fixed hierarchy diagram is **illustrative**, not a complete enumeration of current queue contents or every allowed reparent. Each application is a direct child of exactly one state or in-flight tab at a time. Its nested research tabs move with it. All root, application, in-flight and research tabs count toward the document limit.

## 5. Runtime deployment and external integrations

```mermaid
flowchart LR
  subgraph Host["One GDTQ worker (Python 3.12 / Docker)"]
    Scheduler["Scheduler"]
    O["Orchestrator + typed worker handlers"]
    SQLITE[("Persistent SQLite volume")]
    Scheduler --> O
    O <--> SQLITE
  end
  OAuth["Google OAuth token / refresh credentials"]
  GDocs[("Google Docs API")]
  GDrive[("Google Drive API")]
  ATS["Job-board APIs / scraping"]
  LLM["Model API"]
  Gmail["Gmail API — draft by default"]
  Calendar["Calendar API — opt-in"]
  O --> GDocs
  O --> GDrive
  O --> ATS
  O --> LLM
  O -.-> Gmail
  O -.-> Calendar
  OAuth --> GDocs
  OAuth --> GDrive
  OAuth --> Gmail
  OAuth --> Calendar
```

There is **no Application Service, REST/MCP server, PostgreSQL service or alternate stage-task runner** in this deployment. Live Google integration, browser submission and external send permissions still require explicit configuration and testing.

## Maintenance rule

When changing `gdtq/queue.py`, `dispatch.py`, `downstream.py`, `retention.py`, `config/gdtq-routing.json`, or deployment boundaries, update the relevant diagram in the same pull request. Keep diagrams architecture-level and avoid copying state policy into a second authoritative configuration.
