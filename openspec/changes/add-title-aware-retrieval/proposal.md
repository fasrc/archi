# Change: Add title- and filename-aware document retrieval

## Why
Retrieval routinely misses documents whose query keyword appears in the document
title or filename. Root cause: the ingestion pipeline embeds and full-text indexes
only the chunk body. In `src/data_manager/vectorstore/manager.py` the searchable
text for each chunk is `split_doc.page_content`; the filename is attached only as
`entry_metadata["filename"]` and never injected into the embedded/indexed text. The
full-text column in `src/cli/templates/init.sql` is
`to_tsvector('english', chunk_text)`, so BM25 also never sees the title or filename.
As a result both retrieval paths — semantic (pgvector) and BM25 — are blind to
title/filename text, so a keyword that lives only in the title or filename cannot be
matched.

## What Changes
- Inject a title/source header (derived from `display_name` and `filename`) into each
  chunk's searchable text at ingestion time, so both the embedding and the full-text
  index include title/filename tokens.
- **BREAKING** (schema/migration): Replace the generated full-text column with a
  weighted `tsvector` that gives `display_name` and `filename` weight `A` and
  `chunk_text` weight `B`, so title/filename matches outrank body-only matches. This
  changes the `document_chunks` schema and requires a migration.
- Add a query-time filename/title match boost in hybrid search, reusing the existing
  `pg_trgm` GIN index on `documents.display_name`, so documents whose name matches the
  query are surfaced even when body similarity is low.
- Make the new behavior config-driven (enable/disable header injection and the title
  weight/boost) and add a benchmark to quantify before/after retrieval quality.
- Re-ingestion of existing corpora is required to populate the new searchable text and
  embeddings (documented in the migration plan).

## Impact
- Affected specs: `document-ingestion`, `hybrid-retrieval`
- Affected code:
  - `src/data_manager/vectorstore/manager.py` (chunk header injection)
  - `src/cli/templates/init.sql` (weighted `chunk_tsv` generated column + migration)
  - `src/data_manager/vectorstore/postgres_vectorstore.py` (`hybrid_search` filename boost)
  - `src/data_manager/vectorstore/retrievers/hybrid_retriever.py` (pass-through of boost config)
  - `src/cli/templates/base-config.yaml` (new `data_manager` retrieval knobs)
  - `src/data_manager/vectorstore/loader_utils.py` (expose title/display_name to ingestion if needed)
  - Benchmarking harness under `src/bin/service_benchmark.py` / `docs/` (eval + docs)
