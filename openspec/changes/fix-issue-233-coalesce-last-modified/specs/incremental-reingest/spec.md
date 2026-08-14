## MODIFIED Requirements

### Requirement: The documents catalog persists a last_modified value

The system SHALL provide a nullable `last_modified` column on the Postgres `documents` catalog — present in the base schema (`src/cli/templates/init.sql`) for fresh installs and added to existing databases by a forward-only, idempotent migration under `src/cli/templates/migrations/` (safe to run more than once). `upsert_resource` SHALL store a `last_modified` value carried in a resource's metadata into that column on both insert and conflict-update. When no such value is present, `upsert_resource` SHALL store `NULL` on insert — there is no prior value to keep — and SHALL leave any already-stored value unchanged on conflict-update: an absent incoming timestamp means "no new information", never "clear the column". Rows that predate the column SHALL read as `NULL` with no backfill.

#### Scenario: upsert stores a provided last_modified
- **WHEN** a resource is upserted with `last_modified` in its metadata
- **THEN** the value is written to the `documents.last_modified` column

#### Scenario: Insert without last_modified stores NULL
- **WHEN** a resource with no `last_modified` in its metadata is inserted for the first time
- **THEN** the `documents.last_modified` column is `NULL` and no error occurs

#### Scenario: Conflict-update without last_modified preserves the stored value
- **WHEN** a resource whose stored row already holds a `last_modified` is re-upserted with no `last_modified` in its metadata
- **THEN** the stored value is left unchanged rather than overwritten with `NULL`
- **AND** the statement carries the absent value through as `NULL`, so the preservation is decided by the database and not by the caller omitting a parameter

#### Scenario: Conflict-update with a supplied last_modified still overwrites
- **WHEN** a resource whose stored row already holds a `last_modified` is re-upserted with a different `last_modified` in its metadata
- **THEN** the supplied value replaces the stored one, including when it is older

#### Scenario: A re-ingest that can never supply a timestamp is non-destructive
- **WHEN** a source type that never attaches `last_modified` re-ingests a page a sitemap had previously stamped
- **THEN** that page's stored `last_modified` survives the re-ingest

#### Scenario: Migration is idempotent
- **WHEN** the migration runs against a database that already has the column
- **THEN** it completes without error and the column is unchanged
