## ADDED Requirements

### Requirement: The recorded embedding dimensions match the ingest path's resolution
The ingest config snapshot SHALL record the embedding dimensions with the same resolution the data manager applies: an explicit per-embedder `dimensions` value first, then the per-model default table, then 384.

#### Scenario: OpenAI embedder with no explicit dimensions
- **WHEN** the configured embedder is `OpenAIEmbeddings` and its class-map entry has no `dimensions` key
- **THEN** the snapshot records `embedding_dimensions` as 1536

#### Scenario: HuggingFace embedder with no explicit dimensions
- **WHEN** the configured embedder is `HuggingFaceEmbeddings` and its class-map entry has no `dimensions` key
- **THEN** the snapshot records `embedding_dimensions` as 384

#### Scenario: Explicit dimensions win
- **WHEN** the class-map entry has a `dimensions` value
- **THEN** the snapshot records that value

#### Scenario: The restated table matches the data manager's table
- **WHEN** the data manager's `default_dimensions` table changes
- **THEN** a unit test fails until the snapshot's table matches it

### Requirement: The recorded chunk count is scoped to the active collection
The ingest run record SHALL count only the chunks of the active collection, with the same predicate as `PostgresVectorStore.count()`.

#### Scenario: Rows of an old collection exist
- **WHEN** `document_chunks` holds rows whose `metadata->>'collection'` names a different collection
- **THEN** the recorded `chunk_count` excludes those rows

#### Scenario: The manager passes its collection name
- **WHEN** the data manager records an ingest run
- **THEN** it passes its active collection name to the count query
