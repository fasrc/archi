## Context

`PostgresVectorStore.hybrid_search` (`src/data_manager/vectorstore/postgres_vectorstore.py:406-527`) blends two component scores in one SQL statement:

```sql
1.0 - (c.embedding {distance_op} %s::vector) AS semantic_score,   -- line 485
c.chunk_text <@> to_bm25query(%s, 'idx_chunks_bm25') AS bm25_score, -- line 476/486
...
(semantic_score * %s + COALESCE(bm25_score, 0) * %s) AS combined_score -- line 498
ORDER BY combined_score DESC LIMIT %s                                  -- line 500
```

Timescale `pg_textsearch` documents `<@>` as returning **negative** scores — lower means a better match — so that ascending index scans work. Adding that to a positive semantic score and ordering `DESC` inverts the keyword half.

**Verified live state (dev deployment, `postgres-dev`, `origin/dev` @ `9144918`):**

| Fact | Value | Source |
|---|---|---|
| `pg_textsearch` installed, `idx_chunks_bm25` present | yes | `pg_extension`, `pg_index` |
| Chunks in `document_chunks` | 5450 | `count(*)` |
| BM25 score range (`how do I request a GPU on Cannon`) | **−14.4727 … 0.0000** | live query |
| Keyword-matching chunks (`< 0`) / zero-overlap (`= 0`) | 1463 / 3987 | live query |
| Live `dynamic_config` | `bm25_weight=0.60`, `semantic_weight=0.40`, `use_hybrid_search=true`, `num_documents_to_retrieve=5`, `active_pipeline=FASRCDocsAgent` | `dynamic_config` row `id=1` |

With `bm25_weight=0.60` live — the **higher** of the two weights — any `|bm25| > 0.67` outweighs the entire achievable semantic range (`1.0 × 0.40`). All 1463 keyword-matching chunks therefore sort below all 3987 zero-overlap chunks; keyword matches are categorically excluded whenever at least `k` non-matching chunks exist.

**Why nothing caught it.** Three independent gaps line up:
1. No spec stated any requirement about how the halves combine (`openspec/specs/` has no hybrid-scoring capability), so the inversion violated nothing.
2. Every mocked `bm25_score` in `tests/unit/test_postgres_vectorstore.py` (lines 269, 314, 365, 377, 422, 463) is **positive** — the fixtures encode a convention the backend cannot produce.
3. The only production call site, `hierarchical_retriever.py:133`, reaches `hybrid_search` through a duck-typed `vectorstore` attribute guarded by a runtime `getattr` check (`hierarchical_retriever.py:115-120`). Pyright resolves no reference to `hybrid_search` from there, so no type checker was ever in a position to see the contract.

The blast radius is the default path: `hierarchical_rerank.enabled` defaults true (`base-config.yaml:244-247`, ADR 0003) and builds its `candidate_pool_size=20` pool via `hybrid_search`, so the cross-encoder reranks a pool the blend already stripped of keyword matches.

## Goals / Non-Goals

**Goals:**
- Make the BM25 component contribute positively, so better keyword matches rank higher.
- Put both components on a common `0..1` scale before weighting, so `semantic_weight` / `bm25_weight` mean what they document.
- Handle the degenerate all-equal-BM25 case (common: a query matching no chunk) without dividing by zero.
- Encode the real backend sign conventions in the test fixtures so the inversion cannot silently return.
- Reduce the scattered weight defaults to one source of truth without changing the live effective values.
- Grade the change through the existing RAGAS/goldenset harness and report the numbers honestly.

**Non-Goals:**
- **Restructuring the query for an index scan.** Scoring `<@>` in the SELECT list is a documented anti-pattern that prevents `idx_chunks_bm25` from being used; fixing it means a per-side `ORDER BY … LIMIT` shape, which is a larger rewrite. Deferred so this stays a reviewable bug fix.
- **Adopting RRF.** Decided against for this change (see Decisions) and left as a separately graded follow-up.
- **Re-tuning the weights.** The live 0.60/0.40 pair was never tuned against a working blend; a sweep belongs to the measurement loop, not here.
- **Reconciling the `static_config` / `dynamic_config` table-name collision** discovered while tracing the defaults (see Risks). Out of scope; needs its own issue.
- Any re-ingest, re-embed, or `document_chunks` schema change.

## Decisions

### 1. Min-max normalization, not RRF

Both components are normalized to `0..1` across the scored set, then combined with the existing weighted sum.

*Rationale:* it preserves the contract that `base-config.yaml:242-243`, the `hybrid_search` docstring, the `NUMERIC(3,2)` `dynamic_config` columns, and the `config_service` validation (`0.0 <= weight <= 1.0`, `config_service.py:882`) already assume. That keeps this change a **bug fix** rather than a redefinition of the tuning knobs, which matters because it needs to ship to a live deployment quickly.

*Alternative — Reciprocal Rank Fusion:* fuses by rank, so it is scale-free (immune to future score-scale drift) and permits per-side `ORDER BY … LIMIT` so both indexes are used. Rejected **for now** because it redefines what the weights mean, invalidating the documented semantics and the DB column meaning, making it a behavior change that cannot be justified as a fix. Recommended as the follow-up, graded through the loop this change unblocks.

*Alternative — fix the sign only:* rejected. A negation alone leaves a `0..14.5` term weighted at 0.60 against a `0..1` term at 0.40, so BM25 would dominate in the opposite direction. That trades one wrong ranking for a differently wrong one.

### 2. Normalize **both** halves, not just BM25

It is tempting to treat the semantic score as already `0..1` and scale only BM25. That is unsafe: `distance_metric` is configurable (`postgres_vectorstore.py:111-121`) across `cosine` (`<=>`), `l2` (`<->`), and `inner_product` (`<#>`), and the expression is a flat `1.0 - distance` for all three:

| metric | distance range | `1 - distance` | bounded `0..1`? |
|---|---|---|---|
| `cosine` (default) | `0..2` | `−1..1` | no — negative for opposed vectors |
| `l2` | `0..∞` | `−∞..1` | **no — unbounded** |
| `inner_product` | `−1..1` (normalized) | `0..2` | no |

Only `cosine` is even approximately in range, and only for non-opposed vectors. Normalizing both components makes the blend correct for every supported metric and removes a second latent scale bug.

### 3. Normalize in SQL via window functions, before `LIMIT`

Use `min(...) OVER ()` / `max(...) OVER ()` inside the existing `scored` CTE rather than a separate bounds CTE cross-join.

*Rationale:* one pass over the already-materialized CTE, no additional scan, and it reads as a local transformation of the two score expressions.

*Critically, normalization must happen before `LIMIT`.* Post-processing the fetched rows in Python cannot fix this bug: the broken `ORDER BY … LIMIT` has already discarded every keyword-matching chunk before Python sees anything. Rescaling the survivors would reorder a set from which the right answers are already absent. The ordering must be correct in SQL.

### 4. Degenerate case via `NULLIF` + `COALESCE`

`(score - min) / NULLIF(max - min, 0)` yields `NULL` when every candidate shares one score; `COALESCE(..., 0)` turns that into a constant contribution, so ranking falls back to the other component. This is the *common* path, not an edge case — any query whose terms match no chunk gives all-zero BM25 across the corpus.

`COALESCE` on the raw BM25 term is also retained so a SQL `NULL` from the operator maps to the weakest keyword evidence rather than nulling the whole combined score.

### 5. One constant for the weight defaults

Define the default pair once and have the `hybrid_search` signature, the `HybridRetriever` dataclass, and the `build_vector_retriever` factory fallbacks derive from it. Keep the live effective values (semantic 0.40 / bm25 0.60) so this part changes no behavior.

Tracing the defaults found **three distinct pairs across ten sites**, wider than #205 first recorded:

| Pair (sem/bm25) | Sites |
|---|---|
| 0.7 / 0.3 | `postgres_vectorstore.py:411-412`, `init.sql:168-169` (`dynamic_config`), `config_service.py:89`, `:210`, `:973`, `:1058` |
| 0.4 / 0.6 | `factory.py:50-51`, `base-config.yaml:242-243`, `qa.py:79`, `cms_comp_ops_agent.py:307` |
| 0.5 / 0.5 | `hybrid_retriever.py:33-34` |

The constant lives in the vectorstore layer; retrievers and pipelines import it. The vectorstore must **not** import from `retrievers/`, which would invert the dependency.

## Risks / Trade-offs

- **Min-max is relative to the scored set** → the best candidate always normalizes to exactly `1.0` and the worst to `0.0`, so `combined_score` is not comparable across queries, and a single BM25 outlier compresses everyone else into a narrow band. Mitigation: state the non-comparability in the docstring and spec (a requirement covers it), and audit callers for absolute thresholding — `hierarchical_retriever` and `grading_retriever` consume the tuples for ordering only. RRF is the structural answer and is the recommended follow-up.
- **Normalizing over the full filtered set retains the sequential scan** → no worse than today (the `<@>`-in-SELECT anti-pattern already forces it), and negligible at 5450 chunks, but this query is O(corpus) per search and will not scale. Mitigation: record `EXPLAIN` before/after to prove no regression, and file the index-scan restructure as the follow-up that RRF would also deliver.
- **Retrieval results change for every query on the default path** → this is the intent, but it is not self-evidently an improvement. Mitigation: grade through `archi evaluate` (RAGAS `context_precision` / `context_recall` + SOURCES hit-rate) against the goldenset bank and report before/after in the PR, whatever it shows. Rollback is `git revert` + redeploy; no data migration to unwind.
- **The live 0.60 BM25 weight was never tuned against a working blend** → after the fix, keyword matching goes from actively harmful to *dominant* (0.60 vs 0.40). The corrected blend may still be mis-tuned. Mitigation: treat the weight sweep as the first change graded through the loop; do not silently retune inside this fix, or the measurement cannot attribute the effect.
- **`static_config` is defined twice, incompatibly** → `init.sql:102` creates the deployment schema (the live table: `deployment_name`, `embedding_model`, JSONB blobs), while `config_service.py:195-216` creates a *different* `static_config` carrying `bm25_weight` / `semantic_weight` / `active_pipeline`. Because `CREATE TABLE IF NOT EXISTS` runs second against an existing table, config_service's version is a silent no-op, and the live weights actually live in `dynamic_config` (`init.sql:145-189`). Any read path expecting those columns on `static_config` (`config_service.py:606`, `:757`) would raise `KeyError` against a real deployment. **Confirmed against dev**: the live `static_config` has none of those columns. Mitigation: explicitly out of scope here — do not touch `config_service` DDL in this change; file a separate issue, since conflating it with the sign fix would make the PR unreviewable.
- **Fixture correction may turn other suites red** → correcting the mocked BM25 sign is intended to fail tests that encoded the wrong convention. `tests/unit/test_hierarchical_retriever.py` and `tests/unit/test_retriever_factory.py` share the assumption. Mitigation: treat each failure as a finding, not noise — a test that only passed under the inverted sign was asserting the bug.

## Migration Plan

1. No schema change to `document_chunks`, no re-ingest, no re-embed. Query-time only.
2. `init.sql` weight-default alignment touches `dynamic_config` **column defaults**, which affect fresh deployments only. The live dev row already holds 0.60/0.40 explicitly, so no existing row changes and no data migration is required.
3. Deploy: the deployment container runs a non-editable `pip install .`, so a redeploy is required (`docker cp` is invisible). Use the `archi-dev-deploy-verify` skill — `deploy/fasrc-dev/scripts/redeploy.sh` plus a live chat HTTP-200 smoke test.
4. Verify post-deploy by re-running the reproduction SQL and confirming the `−14.47` chunk now outranks the `0.0000` chunks.
5. Rollback: `git revert` the commit and redeploy. Nothing persisted changes shape.

## Open Questions

- **Does the corrected blend need a weight sweep before it is considered done?** The live 0.60/0.40 predates a working blend. Proposal: ship the fix, measure, then sweep as a separate graded change.
- **How much of the +19% RAGAS attributed to hierarchical rerank (ADR 0003, issue #32 A/B) was the cross-encoder compensating for the poisoned pool?** Worth re-running that A/B after the fix; it may revise the reranker's measured value in either direction.
- **Should `1.0 - distance` be replaced with a per-metric similarity conversion** rather than normalized away? Normalization makes the blend correct, but `1 - l2_distance` remains a misleading expression to read. Candidate for the same follow-up as the index-scan restructure.
- **Are there non-goldenset consumers of `combined_score` as an absolute value** (dashboards, logged thresholds, A/B tooling) that a `0..1` query-relative score would mislead? Audit found none in the retriever paths; benchmark tooling should be double-checked during implementation.
