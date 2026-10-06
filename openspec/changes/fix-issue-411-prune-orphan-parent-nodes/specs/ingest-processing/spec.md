## ADDED Requirements

### Requirement: Re-ingest leaves no unreferenced parent nodes
The system SHALL delete, for each document it re-ingests, every `document_parent_nodes` row of that document that no `document_chunks` row in any collection references through `metadata.parent_id`, after the document's new chunks are written in the same transaction.

#### Scenario: Same document ingested twice
- **WHEN** a document is ingested with hierarchical chunking, and then ingested again
- **THEN** `document_parent_nodes` holds one row per `parent_index` for that document
- **AND** every live chunk's `metadata.parent_id` names a surviving parent row

#### Scenario: A parent that another collection references survives
- **WHEN** a parent row of the document is referenced by a chunk in another collection
- **THEN** the delete keeps that parent row

#### Scenario: A document with no document id
- **WHEN** the catalog returns no `document_id` for the file
- **THEN** the system deletes the unreferenced parents whose metadata `resource_hash` is the file's hash and whose `document_id` is `NULL`

#### Scenario: The parent delete fails
- **WHEN** the parent delete raises for a file
- **THEN** the file rolls back to its savepoint and the document is marked `failed`

### Requirement: Removing a resource deletes its unreferenced parent nodes
The system SHALL, when `_remove_from_postgres` deletes the chunks of a resource hash, also delete the `document_parent_nodes` rows of the document with that `resource_hash` that no chunk in any collection references, before the transaction commits.

#### Scenario: Resource leaves the corpus
- **WHEN** `_remove_from_postgres` removes a resource hash whose parents only its own chunks referenced
- **THEN** those parent rows are deleted

#### Scenario: The parent table does not exist
- **WHEN** `_remove_from_postgres` runs on a database that has no `document_parent_nodes` table
- **THEN** it runs no statement on that table, and the chunk deletes commit

#### Scenario: Resource still referenced from another collection
- **WHEN** `_remove_from_postgres` removes a resource hash, and a chunk in another collection references one of its parents
- **THEN** that parent row is kept

### Requirement: A collection reset clears the parent nodes
The system SHALL truncate `document_parent_nodes`, when the table exists, in the same transaction in which `delete_existing_collection_if_reset` truncates `document_chunks`.

#### Scenario: Reset with hierarchical parents present
- **WHEN** `reset_collection` is on and `document_parent_nodes` exists
- **THEN** the table is truncated after `document_chunks` and before the commit

### Requirement: The committed write paths create the parent-id index
The system SHALL create the index `idx_chunks_parent_id` on `document_chunks ((metadata->>'parent_id'))` with `IF NOT EXISTS` in `ensure_chunks_parent_id_index`, called from the committed setup step of `_add_to_postgres` and before the parent cleanup of `_remove_from_postgres`, and SHALL NOT create it in `ensure_hierarchical_schema`.

#### Scenario: Upgraded volume, removal-only sync
- **WHEN** `_remove_from_postgres` runs and `document_parent_nodes` exists
- **THEN** it executes the `CREATE INDEX IF NOT EXISTS idx_chunks_parent_id` statement before the first parent delete, in the transaction that commits

#### Scenario: Upgraded volume, add path
- **WHEN** `_add_to_postgres` runs with hierarchical chunking
- **THEN** it executes the `CREATE INDEX IF NOT EXISTS idx_chunks_parent_id` statement before the setup commit

#### Scenario: Chat retrieval path
- **WHEN** `ensure_hierarchical_schema` runs on a cursor
- **THEN** it does not execute any statement on `idx_chunks_parent_id`

### Requirement: Parent deletes are logged and leave the fingerprint queries unchanged
The system SHALL log, at INFO, one summary line with the count of parent rows deleted for each `_add_to_postgres` run with hierarchical chunking and for each `_remove_from_postgres` call, and SHALL NOT change the text of the corpus-fingerprint queries.

#### Scenario: Summary line on re-ingest
- **WHEN** a hierarchical re-ingest deletes 3 unreferenced parent rows
- **THEN** one INFO log line reports 3 deleted parent nodes

#### Scenario: Rolled-back file is not counted
- **WHEN** a file deletes parent rows and then its savepoint rolls back
- **THEN** the INFO summary line does not count those rows

#### Scenario: Fingerprint queries unchanged
- **WHEN** the change is applied
- **THEN** the existing corpus-fingerprint tests pass without edits
