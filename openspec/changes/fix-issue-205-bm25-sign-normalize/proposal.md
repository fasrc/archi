## Why

`PostgresVectorStore.hybrid_search` adds Timescale `pg_textsearch`'s BM25 score — which the extension documents as **negative**, where lower means a better match — directly to a positive `1 - cosine_distance` semantic score, then orders `DESC`. The keyword half is therefore sign-inverted: the better a chunk matches the query's keywords, the further down it sorts. Measured on the live dev database (5450 chunks), BM25 scores span **−14.4727 … 0.0000**, so with the effective `bm25_weight=0.6` the penalty outweighs the semantic term's entire achievable range; all 1463 keyword-matching chunks rank below all 3987 zero-overlap chunks, and chunks containing the query's keywords are categorically excluded from results.

This is live in the dev deployment and it poisons the default retrieval path: `hierarchical_rerank` is enabled by default and builds its candidate pool by calling `hybrid_search`, so the cross-encoder reranks a pool from which the keyword matches have already been removed. Hybrid search is currently strictly worse than semantic-only search. No spec states any requirement about how the two score halves combine, which is why nothing caught it.

## What Changes

- Negate the `pg_textsearch` `<@>` term in the `hybrid_search` SQL so a better keyword match yields a **higher** contribution, matching the `ORDER BY combined_score DESC` the query already uses.
- Min-max normalize both the semantic and BM25 components to `0..1` across the scored set before applying weights, so `semantic_weight` and `bm25_weight` express the proportion they already claim to. Without this, un-inverting the sign only reverses which half dominates (BM25 spans 0..14.5 against a 0..1 semantic term).
- Guard the degenerate case where every candidate carries an identical BM25 score (`max == min`), which would otherwise divide by zero — contribute a constant instead so ranking falls back to the semantic half.
- Correct the BM25 sign convention in the mocked fixtures in `tests/unit/test_postgres_vectorstore.py`, which currently assert **positive** `bm25_score` values (0.9, 0.7, 0.5, 0.2 …). These fixtures encode the wrong convention and are the reason the suite passes today.
- Reconcile the three disagreeing semantic/bm25 weight-default pairs — `postgres_vectorstore.py` (0.7/0.3), `hybrid_retriever.py` (0.5/0.5), `factory.py` + `base-config.yaml` (0.4/0.6), `init.sql` (0.7/0.3) — onto one source of truth, preserving the deployment's effective 0.4/0.6 so no behavior changes.
- State the sign convention explicitly in the `hybrid_search` docstring and in `docs/`, so a future reader cannot reintroduce the inversion.
- Record the `EXPLAIN` plan before and after, and note that scoring `<@>` in the SELECT list is a documented anti-pattern that prevents `idx_chunks_bm25` from being used. Restructuring for an index scan is **out of scope** here and left to a follow-up so this change stays a reviewable bug fix.

Not breaking: the `hybrid_search` signature, return type, and the `search_vectorstore_hybrid` tool contract are unchanged. Retrieval *results* change — that is the point — so the change is graded through the existing RAGAS harness rather than asserted.

## Capabilities

### New Capabilities
- `hybrid-search-scoring`: How `PostgresVectorStore.hybrid_search` combines the semantic and BM25 halves into a single ranking — the sign convention each backend score follows, normalization onto a common scale before weighting, the meaning of the configured weights, degenerate-input handling, and a single source of truth for the weight defaults. No existing spec covers this, which is why the inversion violated nothing.

### Modified Capabilities
<!-- None. `hierarchical-rerank-retrieval` consumes hybrid_search but states no requirement
     about how its scores combine, so its requirements are unchanged — its candidate pool
     simply stops being poisoned. `retrieval-benchmarking` is used to grade this change but
     its own requirements do not change. -->

## Impact

**Code**
- `src/data_manager/vectorstore/postgres_vectorstore.py` — the `hybrid_search` scoring SQL (negation + normalization CTE), signature weight defaults, docstring.
- `src/data_manager/vectorstore/retrievers/hybrid_retriever.py` — dataclass weight defaults reference the shared constant.
- `src/data_manager/vectorstore/retrievers/factory.py` — config-read fallbacks reference the shared constant.
- `src/cli/templates/init.sql` — dynamic-config column defaults for `bm25_weight` / `semantic_weight` aligned to the same pair.
- `src/cli/templates/base-config.yaml` — comment noting the normalized-weight semantics; values unchanged.

**Tests**
- `tests/unit/test_postgres_vectorstore.py` — fixtures corrected to the real negative BM25 convention; new regression test that a keyword-matching chunk outranks a zero-overlap chunk.
- `tests/unit/test_hierarchical_retriever.py`, `tests/unit/test_retriever_factory.py` — re-checked for the same wrong-sign assumption in their mocks.

**Behavior / operations**
- Retrieval ranking changes for every query on the default path. Graded through `archi evaluate` (RAGAS `context_precision` / `context_recall` plus SOURCES hit-rate) against the goldenset bank, reported before vs. after.
- Query-time only: **no re-ingest, re-embed, or schema migration** of `document_chunks` is required. The `init.sql` change touches dynamic-config column defaults only.
- Requires a dev redeploy to take effect (the deployment container runs a non-editable `pip install .`).

**Dependencies**
- None added. `argilla` and `ragas` stay out of `pyproject.toml` / `requirements-base.txt` (benchmark-container-only).

**Tracking**
- Closes #205. Supersedes and sharpens Step C of exploration #60; the blend-method decision recorded there is settled as min-max normalization, with RRF left as a separately graded follow-up.
