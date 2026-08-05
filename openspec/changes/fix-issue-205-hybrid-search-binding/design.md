## Context

`PostgresVectorStore.hybrid_search` (`src/data_manager/vectorstore/postgres_vectorstore.py:406-527`) assembles one SQL statement from fragments, then binds parameters by list concatenation. The placeholders occur in this order:

| # | Placeholder | Line |
|---|---|---|
| 1 | `%s::vector` — embedding | 485 |
| 2 | `to_bm25query(%s, …)` — BM25 query | 476, interpolated at 486 |
| 3 | `c.metadata->>'collection' = %s` (+ metadata filters) | 461/466, interpolated at 494 |
| 4 | `semantic_weight`, `bm25_weight` | 498 |
| 5 | `LIMIT %s` | 501 |

Line 505-506 supplies a different order:

```python
# Params order: embedding, collection (+ any filters), query, semantic_weight, bm25_weight, k
all_params = [embedding_str] + params + [query, semantic_weight, bm25_weight, k]
```

The comment states the intent; the SQL disagrees with it.

### Verified live state (dev deployment, `postgres-dev`, `origin/dev` @ `9144918`)

| Fact | Value |
|---|---|
| `pg_textsearch` installed, `idx_chunks_bm25` present | yes |
| Chunks in `document_chunks` | 5450 |
| `metadata->>'collection'` | `default_collection_with_HuggingFaceEmbeddings`, non-null on 5450/5450 |
| Rows from the **mis-bound** `WHERE` | **0** |
| Rows from the **correctly-bound** `WHERE` | **5450** |
| BM25 range with correct binds | **−14.4727 … 0.0000** (1463 rows `< 0`, 3987 at `0`) |
| Effective weights (static YAML, `factory.py:50-51`) | semantic 0.4 / bm25 0.6 |

The 0-vs-5450 contrast is the proof of the binding defect. It matters that this was established **through the code's own parameter order** rather than a hand-authored query: a hand-written statement with correct binds is what produced the initial misdiagnosis, in which the sign inversion was reported as actively degrading live retrieval when it is in fact masked.

### The three defects and their dependency

1. **Binding** — hybrid search returns semantic-only results on every query. Live.
2. **Sign** — `<@>` is negative-oriented; summed into a positive score and ordered `DESC`. Latent behind (1).
3. **Scale** — BM25 spans `0..14.47` against a nominally `0..1` semantic term. Latent behind (1).

**Fixing (1) alone regresses retrieval below today's behavior**, because it activates (2) and (3) at `bm25_weight=0.6`. This dependency is the central constraint on the change.

### Why it survived ~6 months

- The fallback at 513-516 logs nothing, so an inert hybrid search is indistinguishable from a working one.
- No spec covered parameter binding or score combination, so nothing was violated.
- The six mocked `bm25_score` fixtures in `tests/unit/test_postgres_vectorstore.py` (lines 269, 314, 365, 377, 422, 463) are **positive** — a value the backend cannot emit — and mocked rows cannot detect a bind-order error regardless of their values, since the database computes the ranking.
- The only production call site (`hierarchical_retriever.py:133`) reaches `hybrid_search` through a duck-typed attribute guarded by a runtime `hasattr` check (`:115-123`); Pyright resolves no reference to it.

Introduced upstream by `0f894a52` (2026-01-30), which moved the BM25 expression from a second CTE into the first CTE's SELECT list — relocating its placeholder ahead of the `WHERE` — without updating `all_params`. Before that commit the ordering was correct. Reported upstream at archi-physics/archi#542; tracked in #206.

## Goals / Non-Goals

**Goals:**
- Parameters reach their intended placeholders, structurally rather than by convention.
- A degraded fallback is loud.
- BM25 contributes positively, on a scale comparable to the semantic component.
- The degenerate all-equal-BM25 case is safe.
- Tests can actually detect all of the above.
- The fusion method is chosen by measurement against the goldenset, not by assertion.

**Non-Goals:**
- Index-scan restructuring (`<@>` in a SELECT list forces a sequential scan).
- Weight-default reconciliation (three pairs across ten sites) — mechanical, separate PR.
- Re-tuning the weights. `0.4/0.6` predates BM25 ever contributing; a sweep is a later graded change.
- The write-only `dynamic_config` retrieval knobs, the duplicate `static_config` definition, and `HybridRetriever` re-raising the missing-index error — separate issues.
- Any re-ingest, re-embed, or `document_chunks` schema change.

## Decisions

### 1. Make the parameter order structural, not conventional

Extract SQL assembly into a helper returning `(sql, params)`, appending each parameter at the point its fragment is composed. The current failure mode is that fragment order and parameter order are maintained in two places by hand; a comment was the only link between them, and it went stale.

*Alternative — reorder `all_params` to match today's SQL:* a one-line fix that leaves the same trap for the next person who moves an expression. Rejected. The sibling `similarity_search_by_vector_with_score:362` gets this right today only because `insert(0, …)` happens to match its simpler layout.

Returning `(sql, params)` also makes the statement assertable without a database, which is what closes gap 3 above.

### 2. Fix all three defects in one change, binding first

Non-negotiable, per the dependency above. Splitting them across PRs would merge a known regression.

### 3. Normalize both components, in SQL, before `LIMIT`

Min-max normalize semantic and BM25 to `0..1` using `min(...) OVER ()` / `max(...) OVER ()` inside the existing `scored` CTE.

*Both components*, because `1.0 - distance` at line 485 is unconditional while `distance_metric` is configurable (`:111-121`):

| metric | distance range | `1 - distance` | bounded `0..1`? |
|---|---|---|---|
| `cosine` (default) | `0..2` | `−1..1` | no |
| `l2` | `0..∞` | `−∞..1` | **no, unbounded** |
| `inner_product` | `−1..1` | `0..2` | no |

Note `similarity_search_by_vector_with_score:397-401` converts only for cosine and returns a raw distance otherwise, so the two methods already disagree for non-cosine metrics. Normalizing both removes the dependence rather than preserving the disagreement.

*In SQL, before `LIMIT`*, because post-processing fetched rows cannot help: the row limit has already selected which candidates survive. Normalizing the survivors would rescale a set from which the right answers may be absent.

### 4. Degenerate case via `NULLIF` + `COALESCE`

`COALESCE((x - lo) / NULLIF(hi - lo, 0), 0)`. This is the *common* path, not an edge case — any query whose terms match no chunk produces all-zero BM25 across the corpus. Keep the existing `COALESCE` on the raw term so SQL `NULL` maps to weakest-keyword-evidence.

### 5. Choose the fusion method by measurement

Grade sign-corrected min-max against RRF on the same goldenset bank, with a blocking no-regression criterion on `context_precision`, `context_recall`, and SOURCES hit-rate.

Min-max is the **default** because it preserves the contract that `base-config.yaml:242-243`, the docstring, the `NUMERIC(3,2)` DB columns, and the `config_service` validation (`:882`) already assume, keeping this a bug fix rather than a redefinition of the tuning knobs. RRF is scale-free and would additionally permit per-side `ORDER BY … LIMIT` (fixing the sequential scan), but it changes what the weights mean.

The reason to measure rather than assert: this change **activates a retrieval signal that has never contributed**, so it is not an incremental adjustment to a working blend. Neither fusion method can be assumed better than the semantic-only behavior currently in production. Min-max's known weakness — corpus-relative scaling, where one outlier compresses everyone else — is exactly the kind of thing a goldenset run will expose.

## Risks / Trade-offs

- **The fix activates BM25 for the first time; it could be a net loss** → this is the primary risk and the reason for the blocking gate. If all fusion options regress against the semantic-only baseline, do **not** ship: report it and stop. Rollback is `git revert` + redeploy, with nothing persisted to unwind.
- **Min-max is relative to the scored set** → `combined_score` is not comparable across queries, and one BM25 outlier compresses the rest. Mitigation: state non-comparability in the docstring and spec; audited consumers (`hierarchical_retriever`, `grading_retriever`) use the tuples for ordering only. RRF is the structural answer if measurement favors it.
- **Normalizing over the full filtered set keeps the sequential scan** → no worse than today, negligible at 5450 chunks, but O(corpus) per search and it will not scale. Mitigation: record `EXPLAIN` before/after to prove no regression; the restructure is a tracked follow-up.
- **Correcting the fixtures will turn other tests red** → intended. A test that only passed under the inverted sign was asserting the bug. Triage each failure rather than restoring the old value.
- **Integration tests could silently skip** → a `pg_textsearch`-gated skip that swallows the whole suite would recreate the blind spot this change exists to close. Mitigation: the spec requires the skip be reported, and CI must be checked for it explicitly.
- **The weights were never tuned against a working blend** → after the fix, keyword matching goes from absent to weighted 0.6. The blend may be mis-tuned even once correct. Mitigation: do not silently retune inside this fix, or the measurement cannot attribute the effect; sweep separately.
- **Fixing the binding without the sign** → would ship a regression. Mitigation: the spec's guard scenarios fail if either correction is reverted independently.

## Migration Plan

1. No schema change, no re-ingest, no re-embed. Query-time only.
2. Deploy requires a redeploy — the container runs a non-editable `pip install .`, so `docker cp` is invisible. Use `archi-dev-deploy-verify`.
3. Post-deploy verification must confirm the corrected path actually ran: container logs showing hybrid candidate generation **without** the fallback warning, plus a keyword-heavy live query returning keyword-relevant sources in the persisted response. An HTTP 200 is not acceptance — the defect being fixed is precisely one that returns 200 while doing the wrong thing.
4. Rollback: `git revert` + redeploy.

## Open Questions

- **Which fusion method ships?** Resolved by the Plan's measurement step, not in advance. Min-max unless RRF measures materially better.
- **Does the corrected blend need a weight sweep before it is considered done?** Proposal: ship the fix, measure, sweep separately.
- **Should `1.0 - distance` be replaced with a per-metric similarity conversion** rather than normalized away? Normalization makes the blend correct, but the expression stays misleading to read, and it disagrees with the sibling method. Candidate for the same follow-up as the index-scan restructure.
- **Is there a CI environment with `pg_textsearch`** for the integration tests, or does one need provisioning? Determines whether the database-executed coverage actually runs on every PR or only locally.
