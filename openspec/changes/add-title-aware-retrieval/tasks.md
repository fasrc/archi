## 1. Ingestion: title/source header injection
- [x] 1.1 In `src/data_manager/vectorstore/manager.py`, derive title (`display_name`,
      falling back to `Path(filename).stem`) and build a `Title: ...\nSource: ...\n\n`
      header
- [x] 1.2 Prepend the header to each chunk's text before embedding and before it is
      stored as `chunk_text`, for every chunk (not just the first)
- [x] 1.3 Gate header injection behind a new config flag
      (`data_manager.title_header.enabled`, default true)
- [x] 1.4 Ensure stemming, if enabled, is applied symmetrically to the header

## 2. Schema: weighted full-text index
- [ ] 2.1 Update `src/cli/templates/init.sql` so `chunk_tsv` is generated from a weighted
      tsvector (`display_name`/`filename` = `A`, `chunk_text` = `B`)
- [ ] 2.2 Rebuild the `pg_textsearch` BM25 index (or GIN fallback) over the weighted
      expression/column
- [ ] 2.3 Add an idempotent migration to convert existing `document_chunks` tables

## 3. Retrieval: filename/title boost
- [ ] 3.1 In `src/data_manager/vectorstore/postgres_vectorstore.py` `hybrid_search`, add a
      configurable additive boost for `display_name` trigram/`ILIKE` matches using
      `idx_documents_name`
- [ ] 3.2 Thread the boost weight through `HybridRetriever`
      (`src/data_manager/vectorstore/retrievers/hybrid_retriever.py`)
- [ ] 3.3 Add config knobs in `src/cli/templates/base-config.yaml`
      (`data_manager.retrievers.hybrid_retriever.title_weight`, `filename_boost`)

## 4. Migration & re-ingestion
- [ ] 4.1 Document the re-ingest/backfill procedure to re-embed with injected headers
- [ ] 4.2 Verify fresh deployments via `init.sql` and existing deployments via migration
- [ ] 4.3 Document rollback (revert generated column; disable header via config)

## 5. Benchmarking & docs
- [ ] 5.1 Add a query set with title-only and filename-only keyword queries to the
      benchmark harness (`src/bin/service_benchmark.py`)
- [ ] 5.2 Run before/after benchmark; record recall/precision deltas
- [ ] 5.3 Tune title weight and filename boost from results
- [ ] 5.4 Update `docs/` (data sources / benchmarking / configuration) for the new knobs

## 6. Tests & validation
- [ ] 6.1 Unit test: ingested chunks contain title/source tokens in searchable text
- [ ] 6.2 Test: title-only keyword query retrieves the document end-to-end
- [ ] 6.3 Test: filename match boost surfaces a document with low body similarity
- [ ] 6.4 Run `openspec validate add-title-aware-retrieval --strict --no-interactive`
