## Why

`citation_formatter.format_citations` treats incoming scores as **distances** (lower =
more relevant, comments in the code say "represent distances"), but every retrieval path
that feeds it actually returns **similarity** scores where higher is more relevant:

- `PostgresVectorStore.hybrid_search` returns
  `combined_score = semantic_score × w + bm25_score × w`, where
  `semantic_score = 1 − cosine_distance`. The SQL orders by `combined_score DESC`, so
  the first result has the highest value (best match), not the lowest.
- `PostgresVectorStore.similarity_search_with_score` for the cosine metric (the default)
  converts the raw distance to `1 − distance` before returning, again making the first
  result the highest value.
- `LlamaIndexHierarchicalRetriever` attaches `rerank_score` from the FlashRank
  cross-encoder, where scores are cross-encoder logits: higher = more relevant.

Because `format_citations` sorts ascending and keeps the *lowest* score when deduplicating,
it shows citations in reverse order of relevance — least relevant first, most relevant last.
The deduplication also discards the best chunk and retains the worst one when the same
source appears more than once.

Issue #208 was filed when a user noticed citations labelled "relevance: 0.34" appearing
above ones labelled "relevance: 0.91" in chat responses.

## What Changes

- `citation_formatter.format_citations` is corrected so that:
  - deduplication keeps the **highest** (best) score for each source
  - the sorted output lists sources with the highest score first
  - the docstring/comments no longer say "lower = more relevant"
- Existing unit tests that pinned the wrong direction are updated to assert the correct
  behaviour.
- `app.py:get_top_sources` carries the same direction inversion (`np.argsort` ascending)
  and is noted as a follow-up item; it is out of scope for this change because any
  `app.py` line change fails diff-cover (the file is not importable by unit tests).

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
No new capability is added; this is a correctness fix. The observable change is that
citations are now shown with the most relevant source first, matching the order in which
the retriever ranks them.

## Impact

- **Code**: `src/archi/utils/citation_formatter.py` — deduplication comparison and sort
  key reversed. No signature change.
- **Tests**: `tests/unit/test_citation_formatter.py` — two existing test assertions are
  flipped to match the correct direction; no new test files.
- **Schema/DB**: none.
- **Callers**: none change. `format_citations` signature is unchanged; the output order
  changes but no caller depends on a particular order.
- **Trade-off**: sources without a score (`-1.0` sentinel) continue to be listed after
  scored ones, which is correct regardless of direction.
