## ADDED Requirements

### Requirement: Pending migrations are applied before app services start
The rendered compose file SHALL include a one-shot `db-migrate` service that applies every `migrations/*.sql` file, in lexicographic order, against the deployment's Postgres before any application service starts.
The service SHALL wait for Postgres to be healthy (`depends_on: postgres: condition:
service_healthy`) and SHALL NOT restart (`restart: "no"`). `config-seed` and the
data-manager SHALL each wait for it via `condition: service_completed_successfully`, so
neither seeds nor ingests against an un-migrated schema. The service SHALL only be
rendered when Postgres is enabled.

#### Scenario: Migration service is rendered
- **WHEN** the compose template is rendered with Postgres enabled
- **THEN** the output contains a `db-migrate` service with `restart: "no"` that depends on `postgres` with `condition: service_healthy`

#### Scenario: Seed and ingest wait for migrations
- **WHEN** the compose template is rendered with Postgres enabled
- **THEN** both `config-seed` and the data-manager list `db-migrate` under `depends_on` with `condition: service_completed_successfully`

#### Scenario: No Postgres, no migration service
- **WHEN** the compose template is rendered with Postgres disabled
- **THEN** the output contains no `db-migrate` service

### Requirement: Migration files are copied into the deployment directory
The templates manager SHALL copy the `migrations/` directory into the rendered deployment directory alongside `init.sql`, preserving file names, so the `db-migrate` service has files to mount.
The copy SHALL be idempotent across repeated renders into the same directory.

#### Scenario: Migrations land next to init.sql
- **WHEN** the templates manager renders a deployment with Postgres enabled
- **THEN** the deployment directory contains a `migrations/` directory holding every `.sql` file from `src/cli/templates/migrations/`

#### Scenario: Re-render is idempotent
- **WHEN** the templates manager renders twice into the same deployment directory
- **THEN** the second render succeeds and `migrations/` holds the same file set as after the first

### Requirement: Migration files are re-runnable on a fresh deployment
Every file in `migrations/` SHALL succeed when applied to a schema that `init.sql` already created, so a fresh deployment is a no-op rather than a startup failure.
`ALTER TABLE ... RENAME COLUMN` has no `IF EXISTS` form in PostgreSQL, so the renames in
`rename_mid_to_message_id.sql` SHALL be guarded on the old column's presence (and the new
column's absence) rather than issued unconditionally.

#### Scenario: Fresh deployment applies migrations as a no-op
- **WHEN** the migration files are applied to a schema created by the current `init.sql`
- **THEN** every file exits successfully and the schema is unchanged

#### Scenario: Legacy volume is renamed once
- **WHEN** the migration files are applied to a schema whose `feedback` table still has a `mid` column and no `message_id` column
- **THEN** the column is renamed to `message_id`, and re-applying the same file afterwards succeeds without error

### Requirement: A failed migration blocks startup loudly
The `db-migrate` service SHALL stop at the first failing statement and exit non-zero, so a genuinely broken migration prevents dependent services from starting instead of leaving a half-migrated schema.
It SHALL NOT continue past an error or mask a non-zero exit.

#### Scenario: Broken migration halts the stack
- **WHEN** a migration file contains a statement that errors
- **THEN** `db-migrate` exits non-zero and the services that depend on its successful completion do not start

### Requirement: The catalog verifies its required columns at startup
`PostgresCatalogService` SHALL verify at startup that every `documents` column it writes exists, and SHALL raise a `RuntimeError` naming the missing columns when any is absent.
The check SHALL run once during initialization — not per resource — and SHALL replace
reliance on the caller's broad exception handler, which today logs an `UndefinedColumn`
per URL and persists nothing while the ingest reports success.

#### Scenario: Missing column fails fast and names itself
- **WHEN** the catalog initializes against a `documents` table with no `last_modified` column
- **THEN** initialization raises a `RuntimeError` whose message names `last_modified`

#### Scenario: Complete schema initializes normally
- **WHEN** the catalog initializes against a `documents` table containing every column it writes
- **THEN** initialization completes without raising

#### Scenario: The check runs once
- **WHEN** the catalog initializes and then upserts several resources
- **THEN** the schema verification query is issued during initialization only, not once per resource
