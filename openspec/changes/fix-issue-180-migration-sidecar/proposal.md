## Why

`init.sql` only runs when Postgres initializes an empty data directory, so an existing
deployment upgraded to a newer build never receives columns added after its volume was
created. `documents.last_modified` is the live example: the catalog names it
unconditionally in its INSERT (`catalog_postgres.py:238`), the column is absent on
upgraded volumes, psycopg2 raises `UndefinedColumn`, and the scraper's per-URL
`except Exception` (`scraper_manager.py:716`) logs it and moves on — so ingest appears to
succeed while persisting nothing. Two migration files already exist under
`src/cli/templates/migrations/` but nothing in `src/cli/` ever reads, copies, or runs
them (`grep -rn "migrations" src/cli/` returns nothing).

## What Changes

- Add a one-shot `db-migrate` sidecar to the rendered compose file that applies every
  `migrations/*.sql` in lexicographic order against Postgres before any app service
  starts, mirroring the existing `config-seed` precedent.
- Make `templates_manager` copy `migrations/` into the deployment directory alongside
  `init.sql`, so the sidecar has files to mount.
- Order the stack: `db-migrate` waits for Postgres healthy; `config-seed` and
  `data-manager` wait for `db-migrate` to complete successfully.
- **Fix a false premise in the issue body**: `rename_mid_to_message_id.sql` is NOT
  idempotent. Its bare `ALTER TABLE ... RENAME COLUMN mid TO message_id` statements have
  no `IF EXISTS` form in PostgreSQL, so on a fresh deployment (where `init.sql` already
  created `message_id`) they raise `undefined_column`, the sidecar exits non-zero, and
  `service_completed_successfully` never fires — taking the whole stack down. The renames
  are made conditional so the file is genuinely re-runnable.
- Add a startup schema precondition check to `PostgresCatalogService`: verify every
  column it writes exists, and raise a `RuntimeError` naming the missing columns instead
  of letting each row fail silently downstream.

## Capabilities

### New Capabilities
- `schema-migration-provisioning`: how a rendered deployment applies pending SQL
  migrations to an existing Postgres volume before app services start, and how the
  catalog refuses to run against a schema missing columns it writes.

### Modified Capabilities

(none — no existing spec covers compose-time schema provisioning or the catalog's
startup preconditions)

## Impact

- `src/cli/templates/base-compose.yaml` — new `db-migrate` service; `depends_on` edges on
  `config-seed` and `data-manager`.
- `src/cli/managers/templates_manager.py` — copy `migrations/` to the deployment dir.
- `src/cli/templates/migrations/rename_mid_to_message_id.sql` — renames made conditional.
- `src/data_manager/collectors/utils/catalog_postgres.py` — startup column check.
- Deployment behavior: an upgraded stack now runs migrations automatically; a migration
  that genuinely fails now blocks startup loudly rather than corrupting ingest silently.
- No new runtime dependencies — the sidecar reuses the Postgres image, which ships `psql`.
