# AJSS state machine (GDTQ)

The authoritative configuration is [`config/gdtq-routing.json`](../config/gdtq-routing.json). This file documents the contract, not a second editable source of truth.

## Application lifecycle

```text
Discovered -> Bookmarked -> Validated -> Prepared -> Applied -> Interviewing -> Offer
                      \--> Invalidated       \--> Manual Apply Required
                                              \--> Rejected
Any permitted operational state -> Dead Letters Queue -> approved redrive
```

The exact transition graph is the user-defined `ALLOWED_TRANSITIONS` in the JSON configuration. Notable constraints: `Invalidated -> Bookmarked` is its only allowed outgoing transition, `Interviewing -> Interviewing` is legal, and `Rejected`, `Offer`, `Duplicate` are terminal. DLQ redrive is explicitly administrative.

**Administrative terminal completions** include `Failed` (permanently unretrievable posting), `Expired` (retention), and `Ghosted` (silence after Applied). These are outside ordinary agent-proposed transitions. A terminal item is archived in Drive before deletion from the Google Docs journal.

## Agent outcome contract

Every agent result has `agent`, `item_id`, `code`, `execution_status`, `summary`, `data`, and optional `attempt_id`. `execution_status` is `SUCCESS`, `RETRYABLE_FAILURE`, or `PERMANENT_FAILURE`; `code` is the agent-specific enum in `schemas/gdtq/<agent>.schema.json`. `gdtq.policy.StateTransitionEngine` enforces exhaustive enum mapping to legal destinations.

A processing failure does not imply a business rejection. Retryable errors return to the original queue with backoff; permanent read errors complete under `Failed/`, and invalid agent results are quarantined in DLQ. Submission uncertainty is **Manual Apply Required** and requires human reconciliation.

## Queue semantics

Every state queue is a Google Doc root tab; its items are direct sub-tabs. `pop(A)` moves an item under `A-onFlight`, and `push(A,B,item,event)` appends the accepted event and moves directly under B. Agents never invoke queue tools themselves.

See [GDTQ](../gdtq/README.md).
