## ADDED Requirements

### Requirement: Chunk searchable text includes title and source
The ingestion pipeline SHALL include the document title (`display_name`) and filename
in the searchable text of every chunk, so that the chunk embedding and the full-text
index both contain title/filename tokens. Title/filename information SHALL NOT be
stored solely in chunk metadata.

#### Scenario: Title keyword present only in the title
- **WHEN** a document whose title contains a keyword is ingested, and the keyword does
  not appear in the body
- **THEN** each persisted chunk's searchable text contains the title tokens
- **AND** a query for that keyword retrieves chunks of that document

#### Scenario: Header applied to every chunk
- **WHEN** a document is split into multiple chunks
- **THEN** every chunk's searchable text is prefixed with the title/source header, not
  only the first chunk

#### Scenario: Header injection is configurable
- **WHEN** the title/source header injection setting is disabled in configuration
- **THEN** chunks are indexed from body text only, preserving prior behavior

### Requirement: Stemming applied symmetrically to injected header
When stemming is enabled, the ingestion pipeline SHALL apply the same stemming to the
injected title/source header that it applies to chunk body text, and the query path
SHALL stem queries identically.

#### Scenario: Stemming enabled
- **WHEN** `data_manager.stemming.enabled` is true and a document is ingested
- **THEN** the injected header tokens are stemmed with the same tokenizer/stemmer as the
  body
- **AND** a stemmed query keyword matches the stemmed title tokens
