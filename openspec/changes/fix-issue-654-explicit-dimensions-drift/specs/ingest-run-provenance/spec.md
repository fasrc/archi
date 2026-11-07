## ADDED Requirements

### Requirement: The ingest snapshot records whether the embedding dimensions were explicit
The ingest config snapshot SHALL record `embedding_dimensions_explicit` as `True` when the named embedder's `embedding_class_map` entry has a `dimensions` key, and as `False` otherwise.

#### Scenario: Explicit dimensions
- **WHEN** the configured embedder is `OpenAIEmbeddings` and its class-map entry is `{dimensions: 384}`
- **THEN** the snapshot records `embedding_dimensions` as 384 and `embedding_dimensions_explicit` as `True`

#### Scenario: No dimensions key
- **WHEN** the configured embedder is `OpenAIEmbeddings` and its class-map entry has no `dimensions` key
- **THEN** the snapshot records `embedding_dimensions` as 1536 and `embedding_dimensions_explicit` as `False`

### Requirement: Legacy dimension compatibility applies only to snapshots without the explicit record
The drift comparison SHALL skip the old-default 384 `embedding_dimensions` mismatch only when the recorded snapshot has no `embedding_dimensions_explicit` key, and SHALL never report `embedding_dimensions_explicit` itself as drift.

#### Scenario: An explicit 384 is removed
- **WHEN** the recorded snapshot has `embedding_dimensions` 384 and `embedding_dimensions_explicit` `True` for `OpenAIEmbeddings`, and the current snapshot has 1536 for the same model
- **THEN** the comparison reports an `embedding_dimensions` drift entry with current 1536 and at-ingest 384

#### Scenario: A row recorded before the explicit record existed
- **WHEN** the recorded snapshot has `embedding_dimensions` 384 and no `embedding_dimensions_explicit` key for `OpenAIEmbeddings`, and the current snapshot has 1536 for the same model
- **THEN** the comparison reports no `embedding_dimensions` drift

#### Scenario: Only the explicit record changes
- **WHEN** the recorded snapshot has `embedding_dimensions` 1536 with `embedding_dimensions_explicit` `True`, and the current snapshot has 1536 with `embedding_dimensions_explicit` `False`
- **THEN** the comparison reports no drift entry for either key
