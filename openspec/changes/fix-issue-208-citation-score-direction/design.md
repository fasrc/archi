## Context

`format_citations` (`src/archi/utils/citation_formatter.py`) is a pure function that
receives a list of LangChain `Document` objects and a parallel list of float scores, then
formats them into a markdown citations block. It is called by both the native chat UI
path (via `app.py` which passes `retriever_scores` from the QA pipeline) and the OpenAI-
compatible `/v1` endpoint (`openai_compat.py:339,420`).

The score list comes from `qa.py:118`: `"retriever_scores": scores`, where `scores` is
extracted via `zip(*retriever_output)` from the retriever's return value. The active
retriever in production is `HybridRetriever`, which delegates to
`PostgresVectorStore.hybrid_search`. That function computes
`combined_score = semantic_score × bm25_weight + bm25_score × semantic_weight` and orders
by `combined_score DESC` (`:500`), so the scores are already in descending-relevance order
when they reach `format_citations`. The function then re-sorts them ascending, reversing
the retriever's ordering.

The same reversal appears in `app.py:get_top_sources` (`np.argsort(scores)` ascending),
but that function lives in a file the unit-test suite cannot import, so fixing it here
would break the diff-cover gate. It is tracked separately.

## Goals / Non-Goals

**Goals**: correct the direction in `format_citations` so the most relevant source is
listed first; keep the `−1.0` sentinel (no-score) entries after scored ones; remove the
misleading "distances" comment.

**Non-Goals**: changing the score type or range; changing the `-1.0` sentinel protocol;
fixing `app.py:get_top_sources` (separate diff-cover constraint); touching
`LlamaIndexHierarchicalRetriever` or any retriever internals.

## Decisions

### D1 — Reverse the deduplication comparator

Current (wrong):
```python
if score != -1.0 and (existing_score == -1.0 or score < existing_score):
    best_by_name[display_name]["score"] = score
```

Correct:
```python
if score != -1.0 and (existing_score == -1.0 or score > existing_score):
    best_by_name[display_name]["score"] = score
```

`score > existing_score` keeps whichever score is numerically larger, i.e., the chunk
that the retriever judged most relevant. The sentinel guard is unchanged — a real score
always wins over `−1.0`.

### D2 — Reverse the sort key

Current (wrong, ascending — lowest first):
```python
entries = sorted(
    best_by_name.items(),
    key=lambda item: (
        0 if item[1]["score"] != -1.0 else 1,
        item[1]["score"] if item[1]["score"] != -1.0 else 0,
    ),
)
```

Correct (descending — highest first):
```python
entries = sorted(
    best_by_name.items(),
    key=lambda item: (
        0 if item[1]["score"] != -1.0 else 1,
        -item[1]["score"] if item[1]["score"] != -1.0 else 0,
    ),
)
```

Negating the score inside the key flips the numeric comparison for scored entries while
keeping the two-tier structure (scored before unscored). An alternative is `reverse=True`
on `sorted`, but that would also reverse the no-score tier relative to the scored tier,
which is unwanted.

### D3 — Update tests rather than the implementation to match old tests

Two tests in `tests/unit/test_citation_formatter.py` pin the wrong direction:

- `test_sorting_lower_is_better` (`:90`) asserts the lower-score entry comes first.
  Rename to `test_sorting_higher_is_better` and flip the assertion.
- `test_duplicate_chunks_deduplicated_best_score_kept` (`:63`) asserts `(relevance: 0.50)`
  is kept (the lower score). Flip to assert `(relevance: 0.90)` is kept.

The implementation must match the spec; the tests must match the implementation. Editing
the tests to silence a finding would be wrong — editing them because they tested the wrong
behaviour is correct. Both tests were testing an incorrect assumption, not correct
behaviour that the implementation should continue to satisfy.

### D4 — No change to the `−1.0` sentinel or the two-tier ordering

The `−1.0` sentinel means "no score available". Keeping no-score entries after scored ones
is correct regardless of direction: a scored entry is strictly more informative than an
unscored one. The only change is the relative order *within* the scored tier.

### D5 — `app.py:get_top_sources` is out of scope

`get_top_sources` also sorts `np.argsort(scores)` ascending and filters with
`score > similarity_score_reference`, which is also inverted. However, `app.py` is
excluded from unit-test imports, so any changed line there fails diff-cover. The
practical impact is also lower: the `similarity_score_reference` default is 10, which
exceeds any similarity score in [0, 1] and is therefore a no-op filter. The sort
direction error remains, but correcting it requires extracting a tested helper module
(a separate PR). This is tracked as a follow-up.

## Risks / Trade-offs

- **Existing callers that depend on ascending order**: None. `format_citations` returns a
  markdown string; no caller parses the order programmatically.
- **Diff-coverage**: The changed lines are in `citation_formatter.py`, which is imported
  by `test_citation_formatter.py`. Coverage is straightforward.
- **`−1.0` no-score entries in the no-score tier**: their relative order within the tier
  is determined by the placeholder `0` in the key; reversing the scored tier does not
  affect them.

## Migration Plan

None. This is a pure logic correction in a pure function. No data migration, no schema
change, no deploy ordering. The change takes effect the next time the code runs.

## Open Questions

None.
