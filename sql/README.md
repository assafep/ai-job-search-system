# SQL migrations — legacy Application Service

This directory contains PostgreSQL/Neon schema and migrations for the **previous Application Service, completed postings and stage-task persistence**. It is not the GDTQ dispatcher database.

**Current GDTQ:** `gdtq.store.WorkIndex` creates its SQLite tables (`identities`, `items`, `checkpoints`, `event_log`) at runtime. The DB needs durable backup and a single-writer deployment strategy. Completed application event-history files are stored in Google Drive, not automatically migrated into PostgreSQL.

Do **not** run these Postgres migrations expecting to provision the new GDTQ index or queue state. Keep them only as long as the compatibility Application Service and its historical data are needed. The numbered migrations remain immutable history; deleting or repurposing them could make existing deployments unrecoverable.

For current architecture see [GDTQ](../gdtq/README.md) and [storage](../docs/storage-backends.md).
