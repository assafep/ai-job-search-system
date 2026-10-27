# Neon migration runbook

Target: existing Neon project `gentle-dawn-59071246`, main branch, database
`ajss-db`. The old schema remains read-only for history; never delete
`applications`, `posting_keys` or `completed_snapshots` during this cutover.

At initial read-only inspection: 160 historical applications, 240 posting
keys, 22 completed snapshots, 46 applications without any posting key.
All are terminal Rejected/Invalidated. Rebuilt identities use both exact
legacy URL and `lower(trim(company)) || ' / ' || lower(trim(role))`.

Apply `001_neon_index.sql` **only to a temporary test branch first** with a
role authorized to create tables in schema `public`. Run the read-only
queries in `002_verify.sql`, confirm zero missing applications and zero
lost legacy keys, and re-run the migration to prove idempotency. Only then
seek approval to apply to production. Do not connect the scheduler to the
database until the schema passes validation.

The current ChatGPT Neon connection is pinned to `ajss_db_migration`,
which does not have `CREATE` privileges on schema `public`, so the
prepare-migration operation was denied; **no migration was applied**.
A Neon administrator must grant the necessary scoped permission or connect
with a schema-owning database role. Do not elevate credentials in source.
