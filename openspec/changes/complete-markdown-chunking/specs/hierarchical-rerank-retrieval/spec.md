# Hierarchical Rerank Retrieval Specification (delta)

## MODIFIED Requirements

### Requirement: Structural parent-child chunking at ingestion

Ingestion SHALL split documents with a structure-aware parser that produces small embedded **child** nodes linked to larger **parent** nodes, replacing fixed-character splitting. The parser SHALL default to sentence-aware splitting; the opt-in `markdown` strategy for Markdown sources is specified by the `markdown-structural-chunking` capability. The target **parent** and **child** node sizes SHALL be configurable via `data_manager.chunking` (`parent_chunk_size` / `child_chunk_size`); when unset, the system SHALL fall back to its built-in defaults, preserving prior behavior. A configured child size below the splitter's built-in default overlap SHALL work: the ingestion path SHALL clamp the effective overlap below the chunk size instead of failing documents.

#### Scenario: Document produces linked parent and child nodes

- **WHEN** a document is ingested
- **THEN** one or more child nodes are created with embeddings and each child references exactly one parent node that contains it

#### Scenario: Child boundaries respect structure

- **WHEN** a document is split into child nodes
- **THEN** child text is segmented on sentence/structural boundaries (not a fixed character count) so individual sentences are not split across children

#### Scenario: Configured chunk sizes drive the parser

- **WHEN** `data_manager.chunking.parent_chunk_size` and/or `child_chunk_size` are set in the config
- **THEN** ingestion parses documents using those target sizes rather than the built-in defaults

#### Scenario: Omitted chunk sizes preserve existing behavior

- **WHEN** `parent_chunk_size` / `child_chunk_size` are absent from the config
- **THEN** ingestion uses the built-in default sizes, producing the same chunking as before this change

#### Scenario: Small configured child size ingests documents

- **WHEN** `child_chunk_size` is set below 200 (the splitter's built-in default overlap)
- **THEN** documents ingest with children at the configured size, and no document fails from an overlap-exceeds-chunk-size error
