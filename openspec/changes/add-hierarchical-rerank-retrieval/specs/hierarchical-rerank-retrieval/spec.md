## ADDED Requirements

### Requirement: Structural parent-child chunking at ingestion

Ingestion SHALL split documents with a structure-aware parser that produces small embedded **child** nodes linked to larger **parent** nodes, replacing fixed-character splitting. The parser SHALL default to sentence-aware splitting and MAY use markdown-element parsing for markdown sources.

#### Scenario: Document produces linked parent and child nodes

- **WHEN** a document is ingested
- **THEN** one or more child nodes are created with embeddings and each child references exactly one parent node that contains it

#### Scenario: Child boundaries respect structure

- **WHEN** a document is split into child nodes
- **THEN** child text is segmented on sentence/structural boundaries (not a fixed character count) so individual sentences are not split across children

### Requirement: Additive parent-node storage preserves the existing chunk schema

Parent nodes SHALL be stored in a dedicated table separate from `document_chunks`. The `document_chunks` table SHALL continue to hold only embedded child/leaf rows with its existing columns and `UNIQUE(document_id, chunk_index)` constraint unchanged. Parent nodes SHALL NOT be embedded and SHALL NOT be added to any vector or BM25 index used by `document_chunks`.

#### Scenario: Existing chunk schema is unchanged

- **WHEN** the new storage is deployed
- **THEN** `document_chunks` retains its existing columns, constraints, and indexes, and existing reads against it behave identically to `main`

#### Scenario: Parents never appear in shared hybrid search

- **WHEN** `PostgresVectorStore.hybrid_search` runs for any query
- **THEN** no parent node is returned among its results (parents are not embedded and not BM25-indexed)

### Requirement: Embedding-model consistency

Child nodes and query text SHALL be embedded with archi's configured embedding model (`sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions). The ingestion path SHALL NOT embed child nodes with any model other than archi's configured embedder.

#### Scenario: Child embedding dimension matches the column

- **WHEN** a child node is embedded for storage
- **THEN** the embedding has 384 dimensions matching the `document_chunks.embedding` column, and a mismatch raises an error rather than storing a wrong-dimension vector

#### Scenario: Query embedded with the same model

- **WHEN** a retrieval query is embedded
- **THEN** it uses the same configured model used for child nodes

### Requirement: Hierarchical retrieval with cross-encoder reranking

The system SHALL provide a retriever that generates candidates via the existing hybrid (BM25 + vector) child search, maps child hits to their parent nodes (deduplicating parents), reranks the candidates with a CPU cross-encoder, and returns the top-ranked parent nodes as context.

#### Scenario: Child hit returns parent context

- **WHEN** a child node matches a query
- **THEN** the retriever returns the child's parent node text as the result context (not the bare child)

#### Scenario: Rerank narrows a larger candidate pool

- **WHEN** the retriever gathers a candidate pool larger than the final result count (e.g., ~20 candidates)
- **THEN** a cross-encoder reranks them and the retriever returns the top results (e.g., 5)

#### Scenario: Duplicate parents are merged

- **WHEN** multiple child hits share the same parent
- **THEN** that parent appears at most once in the returned results

### Requirement: Drop-in retriever behind the existing tool seam

The reranking retriever SHALL be a `langchain_core` `BaseRetriever` returning `Document` objects (optionally as `(Document, score)` tuples) so it integrates through the existing `create_retriever_tool` without changes to the agent loop, prompt templates, or the `search_vectorstore_hybrid` tool name/signature.

#### Scenario: Agent and tool contract unchanged

- **WHEN** the new retriever replaces `HybridRetriever` in the agent's retriever wiring
- **THEN** the agent still exposes a tool named `search_vectorstore_hybrid` with the same input/output contract and no agent or prompt code changes

#### Scenario: Reranking can be disabled by configuration

- **WHEN** the reranking/hierarchical retrieval feature is disabled via configuration
- **THEN** retrieval falls back to the existing hybrid retriever behavior
