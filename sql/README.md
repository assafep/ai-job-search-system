# Common PostgreSQL artifacts

`schema.sql` is the initial PostgreSQL DDL imported unchanged from
the bootstrap project's `sql/schema.sql`. It is independent of Vercel, Neon, or Docker;
it is not portable to other database engines. The main project owns this artifact
for the lifecycle integration. The bootstrap project keeps its current copy until its
provider implementation is migrated, so its standalone command remains usable.

This is a baseline, not a migration engine. `CREATE TABLE IF NOT EXISTS` does not
repair an existing table with incompatible columns. The future
`apply_database_schema` hook must validate existing structure, track versions and
checksums, and apply migrations transactionally under a lock. Roles, credentials,
provider resource setup and data imports do not belong in this file. Runtime app
credentials must not own these objects or perform DDL.

`migrations/003_completed_postings.sql` adds `posting_keys` and `event_attributes`. A normalized URL and a normalized `company / role` both point at one application, so the same role on another job board counts as already applied. `completed_postings` answers whether that application reached a completed state. Journal events stay as written in `events.body`. Parsed links and idempotency keys go in `event_attributes` only when the event has them.

These tables are not connected to the current application service yet. Adding
them does not migrate the Google Docs journal or enable PostgreSQL persistence.

## Stage task cutover

`004_stage_identity.sql` is a historical, already-applied metadata migration.
`005_stage_task_names.sql` performs the breaking cutover to the stage/task
contract. It renames the operational request resource, recursively converts its
identity fields and preserves task IDs, claims, statuses and archived records.
Old names appear only as migration inputs, not as runtime API or loader aliases.

Stop execution, deploy the new code with execution disabled, apply the numbered
migration, verify task counts and identities, refresh MCP tools, and only then
activate execution. See [the cutover procedure](../docs/stage-task-cutover.md).
The migration needs SELECT/UPDATE on `installation_resources` and the existing
ledger permissions. It performs no DDL and does not alter applications, events,
artifacts, completed snapshots, or the Google journal.
