## Context
Archi retrieval misses documents when the query keyword appears in the title or
filename but not prominently in the body. Investigation traced this to the searchable
representation, not retriever tuning:

- `src/data_manager/vectorstore/manager.py` builds each chunk's searchable text from
  `split_doc.page_content` only. `filename` and other file-level fields are stored in
  the chunk `metadata` JSONB but never embedded or indexed.
- `src/cli/templates/init.sql` generates the full-text column as
  `to_tsvector('english', chunk_text)` (with a `pg_textsearch` BM25 index where
  available, falling back to a GIN index on that tsvector). Title/filename text is
  absent from this column.
- `qa.py` already defaults to `HybridRetriever` (BM25 weight 0.6 / semantic 0.4), so
  the wiring is correct; only the indexed data is deficient.

Both retrieval paths therefore operate purely on the document body, which fully
explains the symptom.

## Goals / Non-Goals
- Goals:
  - Make title/filename tokens part of both the embedding and the full-text index.
  - Rank title/filename matches above body-only matches.
  - Keep the change config-driven and private/offline-friendly (no new external services).
  - Quantify the improvement with the existing benchmark harness.
- Non-Goals:
  - Introducing a knowledge graph or GraphRAG (separate proposal).
  - Replacing the embedding model or vector backend.
  - Re-architecting chunking strategy beyond the header injection (overlap tuning is
    optional and called out, not required).

## Decisions
- Decision: Inject a contextual header into each chunk's searchable text at ingestion.
  Format: `Title: {display_name}\nSource: {filename}\n\n` prepended to `chunk` before
  embedding and before it is stored as `chunk_text`. Applied to every chunk (not just
  chunk 0) so any chunk of a document is matchable by its title.
  - Why: This is the single fix that repairs both the semantic and BM25 paths, since
    both derive from the chunk text. It is the standard "contextual chunk header"
    technique.
  - Alternatives considered:
    - Embed title separately as a per-document vector: adds a second index and fusion
      logic; more moving parts than prepending.
    - Metadata-only filtering: does not help semantic recall and is brittle.

- Decision: Replace the generated full-text column with a weighted tsvector:
  ```sql
  chunk_tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(metadata->>'display_name','')), 'A') ||
    setweight(to_tsvector('english', coalesce(metadata->>'filename','')), 'A') ||
    setweight(to_tsvector('english', chunk_text), 'B')
  ) STORED
  ```
  - Why: Gives title/filename hits a strong `ts_rank` boost even for already-ingested
    rows, independent of re-embedding. Complements (does not replace) the header
    injection.
  - Alternatives considered: Doing nothing at the DB layer and relying solely on header
    injection — weaker ranking signal and requires full re-embed to take effect.
  - Note: Where the `pg_textsearch` BM25 index is used instead of the GIN fallback, the
    index must be (re)built over the equivalent weighted expression/column.

- Decision: Add an optional query-time filename/title boost in
  `PostgresVectorStore.hybrid_search`, reusing the existing
  `idx_documents_name` (`gin_trgm_ops` on `documents.display_name`). Documents whose
  `display_name` trigram/`ILIKE`-matches the query receive an additive score boost in
  the hybrid fusion.
  - Why: A safety net that surfaces obvious filename hits even when both body BM25 and
    semantic similarity are low.
  - Alternatives considered: Exact-match only (misses partial/fuzzy filename hits);
    trigram matching is already supported by the existing index.

## Risks / Trade-offs
- Re-embedding cost: header injection only takes effect for chunks embedded after the
  change. → Provide a re-ingest/backfill path and document it; the weighted tsvector
  improves ranking for existing rows in the meantime.
- Schema migration on `document_chunks`: dropping/recreating a generated column and its
  index requires a migration and can be slow on large tables. → Ship an idempotent
  migration; run during a maintenance window; `init.sql` covers fresh deployments.
- Title noise in chunk text: prepending the header slightly inflates token counts and
  surfaces title text to the LLM. → Header is short; acceptable and often helpful for
  grounding.
- Stemming symmetry: if `data_manager.stemming.enabled` is true, ingestion stems
  `chunk_text`; the query path must stem identically or BM25 matches break. → Verify and
  document symmetric stemming; the injected header must follow the same stemming rule.
- Weight tuning: weight `A` vs `B` and the trigram boost magnitude may over- or
  under-rank titles. → Expose as config; tune against the benchmark.

## Migration Plan
1. Land code + config behind flags (default on for fresh deploys).
2. Apply DB migration replacing `chunk_tsv` with the weighted expression and rebuilding
   the full-text/BM25 index. Idempotent; safe to re-run.
3. Trigger re-ingestion (or a backfill that re-embeds with the injected header) per
   deployment; document the CLI invocation.
4. Run the benchmark before and after; record recall/precision deltas.
5. Rollback: revert the generated-column definition to
   `to_tsvector('english', chunk_text)` and disable header injection via config; prior
   embeddings remain valid.

## Open Questions
- Should header injection apply to all source types (git code, tickets) or only file-
  backed sources? Default: all, since `display_name`/`filename` are populated for each.
- Default values for title weight and trigram boost — set provisional defaults, finalize
  from benchmark results.
