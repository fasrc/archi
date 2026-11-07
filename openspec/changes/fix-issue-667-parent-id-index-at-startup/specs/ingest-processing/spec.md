## ADDED Requirements

### Requirement: The data-manager start is the only path that builds the parent-id index
The data-manager service SHALL build the index `idx_chunks_parent_id` on `document_chunks ((metadata->>'parent_id'))` once at start, after `PostgresServiceFactory.set_instance` and before it builds the `DataManager`, with the plain `CREATE INDEX IF NOT EXISTS` statement in its own committed transaction, only when `document_parent_nodes` exists, and no request or ingest path SHALL run that statement.

#### Scenario: Upgraded volume with the parent table
- **WHEN** the data-manager service starts and `document_parent_nodes` exists
- **THEN** it executes `CREATE INDEX IF NOT EXISTS idx_chunks_parent_id` without `CONCURRENTLY` and commits before it builds the `DataManager`

#### Scenario: Volume without the parent table
- **WHEN** the data-manager service starts and `document_parent_nodes` does not exist
- **THEN** it executes no DDL, logs at INFO that it skipped the build, and continues the start

#### Scenario: The index build fails at start
- **WHEN** the database connection or the `CREATE INDEX` statement raises at data-manager start
- **THEN** the service logs a WARNING that names `idx_chunks_parent_id`, commits nothing, and continues the start

#### Scenario: Removal and ingest paths run no index DDL
- **WHEN** `_remove_from_postgres`, the hierarchical setup step of `_add_to_postgres`, or `delete_unreferenced_parents_for_resources` runs
- **THEN** it executes no statement on `idx_chunks_parent_id`, and its parent deletes still remove every parent row that no chunk references

#### Scenario: Chat retrieval path
- **WHEN** `ensure_hierarchical_schema` runs on a cursor
- **THEN** it does not execute any statement on `idx_chunks_parent_id`
