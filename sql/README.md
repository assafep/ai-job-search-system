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
