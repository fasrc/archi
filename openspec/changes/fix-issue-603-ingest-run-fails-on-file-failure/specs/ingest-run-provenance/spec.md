## ADDED Requirements

### Requirement: A system error on any document fails the ingest run
The vectorstore sync SHALL record the ingest run as `failed`, not `updated`, when one or more documents hit a system error while they are added, and a content outcome SHALL NOT change the run status. A system error is an exception while the file is loaded or processed, an exception while it is embedded, or a store failure (a rolled-back savepoint or a database error). A content outcome is "Unsupported file format" or "No text chunks could be extracted"; such a document stays a per-file `failed` catalog row. A document that fails SHALL NOT stop the other documents of the batch from being stored.

#### Scenario: A load error fails the run
- **WHEN** a sync adds two documents and the loader raises for the first one
- **THEN** the ingest run is recorded as `failed`
- **AND** the first document is marked `failed` in the catalog with the exception text

#### Scenario: A processing error fails the run
- **WHEN** a sync adds two documents and an exception escapes the processing of the first one (for example, the text splitter raises)
- **THEN** the ingest run is recorded as `failed`

#### Scenario: An embed error fails the run
- **WHEN** a sync adds two documents on the flat path and the embedding model raises for the first one
- **THEN** the ingest run is recorded as `failed`

#### Scenario: A store failure fails the run
- **WHEN** a sync adds two documents and the store step of the first one raises, so its savepoint is rolled back
- **THEN** the ingest run is recorded as `failed`
- **AND** `update_vectorstore` returns `failed`

#### Scenario: A hierarchical store failure fails the run
- **WHEN** a sync adds two documents with hierarchical chunking and the store step of the first one raises
- **THEN** the ingest run is recorded as `failed`

#### Scenario: The other documents of a mixed batch are still stored
- **WHEN** a sync adds two documents and the first one hits a system error
- **THEN** the second document is still marked `embedded`

#### Scenario: Every document hits a system error
- **WHEN** a sync adds two documents and both hit a system error
- **THEN** the ingest run is recorded as `failed`

#### Scenario: An unsupported format does not fail the run
- **WHEN** a sync adds two documents, no loader supports the first one, and the second one is stored
- **THEN** the ingest run is recorded as `updated`
- **AND** the first document is marked `failed` with "Unsupported file format"

#### Scenario: A document with no text does not fail the run
- **WHEN** a sync adds two documents, the first one gives no text chunks, and the second one is stored
- **THEN** the ingest run is recorded as `updated`
- **AND** the first document is marked `failed` with "No text chunks could be extracted"

#### Scenario: All documents are stored
- **WHEN** a sync adds two documents and both are stored
- **THEN** the ingest run is recorded as `updated`
