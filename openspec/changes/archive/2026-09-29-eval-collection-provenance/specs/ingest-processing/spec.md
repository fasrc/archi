## ADDED Requirements

### Requirement: Chunks carry the embedding model identity
Every chunk written to the vectorstore SHALL carry `metadata["embedding_model"]`, set to the model identifier of the embedding that produced its vector, at the data manager's flat and hierarchical write sites and in `PostgresVectorStore.add_texts()`.

#### Scenario: Flat chunk write
- **WHEN** the data manager embeds a file through the flat chunk path
- **THEN** each chunk's metadata contains `embedding_model` equal to the configured model identifier

#### Scenario: Hierarchical chunk write
- **WHEN** the data manager embeds a file through the hierarchical parent-child path
- **THEN** each child chunk's metadata and each parent node's metadata contain `embedding_model` equal to the configured model identifier

#### Scenario: Direct vectorstore write
- **WHEN** a caller writes chunks through `PostgresVectorStore.add_texts()`
- **THEN** each chunk's metadata contains `embedding_model` from the store's configured identity, next to `collection`

#### Scenario: Model without a model kwarg
- **WHEN** the configured embedding class has neither `model_name` nor `model` in its kwargs
- **THEN** `embedding_model` equals the embedding class name
