## Why

`PostgresVectorStore.hybrid_search` binds its SQL parameters in an order that does not match its own placeholders. The `%s` for `to_bm25query()` sits **before** the `WHERE` placeholders in the generated SQL, but `all_params` supplies collection-then-query (`postgres_vectorstore.py:485-506`). So the BM25 query receives the **collection name**, and the user's question is bound as the **collection equality predicate**.

Measured on the `postgres-dev` snapshot (2026-08-05, `origin/dev` @ `9144918`): every one of 5450 chunks carries a non-null collection, so the mis-bound `WHERE` matches **0 rows** where the correctly-bound one matches **5450**. Zero rows trips the unlogged empty-result guard at lines 513-516, which returns `similarity_search_with_score`. **On that deployment, hybrid search silently degrades to semantic-only for every query and BM25 contributes nothing.** `use_hybrid_search=true` in `dynamic_config` is effectively a lie, and because the fallback logs nothing, the failure is invisible.

Scope that claim to the measured configuration rather than generalizing it. The predicate is `(collection = %s OR collection IS NULL)`, so the zero-row outcome depends on the collection metadata actually present:

| Collection metadata | Mis-bound behavior |
|---|---|
| All non-null (**measured: 5450/5450 on dev**) | 0 rows → silent semantic-only fallback, corpus-wide |
| All null | Every row passes; BM25 scores against the *collection name*, so ~0 for nearly every chunk → effectively semantic ordering, with the sign defect live for any chunk whose text happens to contain the collection name |
| **Mixed** | Only the null-collection subset passes → hybrid runs, but over a **silent subset of the corpus** — a distinct and arguably worse failure than the fallback |
| Query string equals the collection value | Degenerate exception; rows pass |

Only the first row is measured here. The mixed case is the one to be careful about: it does not fall back, so it produces results without any signal that most of the corpus was excluded. Whether any deployment is in that state is unverified, and claims about other deployments' historical retrieval baselines should not be made on this evidence alone.

Behind that defect sit two more, currently masked. The `pg_textsearch` `<@>` operator [returns negative scores](https://github.com/timescale/pg_textsearch/blob/main/README.md) (lower = better, for ascending index scans); line 498 adds that to a positive semantic score and line 500 orders `DESC`, so better keyword matches would sort *lower*. And the two components are never put on a common scale — BM25 spans `0..14.47` after negation while the semantic term is nominally `0..1`.

This matters now because fixing only the binding would **make retrieval worse than today**: it would switch on a sign-inverted, unnormalized keyword term at the effective `bm25_weight=0.6`. The three defects have to be corrected together.

The same defect exists upstream in `archi-physics/archi` and is tracked for reporting in #206; this change is the FASRC-side fix.

## What Changes

- **Fix the parameter binding** so the values line up with the placeholders positionally. Build the parameter list in the same pass that assembles the SQL fragments so the two cannot drift apart again — the sibling method `similarity_search_by_vector_with_score:362` avoids this by using `params.insert(0, …)`; `hybrid_search` is the one place the pattern was open-coded.
- **Make the failure loud.** Add a `logger.warning` at the empty-result fallback naming the query and `k`. A silently-inert hybrid search must never again look healthy.
- **Extract SQL construction** into a helper returning `(sql, params)` so tests can assert on both without a database.
- **Negate the BM25 term** so higher means a better match, matching the existing `ORDER BY … DESC`.
- **Min-max normalize both components** to `0..1` across the scored set, inside the CTE and before the `LIMIT`, so the configured weights express the proportions they document. Both, not just BM25: `1.0 - distance` at line 485 is applied unconditionally while `distance_metric` may be `l2` (unbounded) or `inner_product` (`0..2`), so the semantic half is not reliably `0..1` either.
- **Guard the degenerate case** where every candidate shares one BM25 score — the common path for a query matching no chunk — so it falls back to semantic-only ordering instead of dividing by zero.
- **Select the fusion method by measurement, not assertion.** Grade sign-corrected min-max against RRF on the goldenset bank with a blocking no-regression criterion. Min-max is the default because it preserves the documented meaning of the weight knobs; adopt RRF only if it measures materially better.
- **Replace the test strategy.** `hybrid_search` delegates all scoring and ordering to PostgreSQL and returns `row["combined_score"]` verbatim, so mocked-row fixtures cannot detect a bind-order error, a missing negation, or a broken normalization guard. Add generated-SQL and parameter-order assertions plus tests that execute against real `pg_textsearch`, and correct the six mocked `bm25_score` fixtures that encode a **positive** sign the backend cannot emit.
- **Document the contract** — sign convention, normalization, and that `combined_score` is query-relative and not comparable across queries.

Not breaking: the `hybrid_search` signature, return type, and the `search_vectorstore_hybrid` tool contract are unchanged. Retrieval *results* change on every query — that is the point, and it is why the change is graded rather than asserted.

## Capabilities

### New Capabilities
- `hybrid-search-scoring`: the contract `hybrid_search` must satisfy — that supplied parameters correspond positionally to the SQL placeholders they fill, that a degraded fallback is observable rather than silent, that each component score is oriented higher-is-better and normalized onto a common scale before weighting, what the configured weights mean, degenerate-input handling, and that tests encode the backends' real score conventions. No existing spec covers any of this, which is why three defects in one function violated nothing.

### Modified Capabilities
<!-- None. `hierarchical-rerank-retrieval` consumes hybrid_search but states no requirement
     about parameter binding or score combination, so its requirements are unchanged — its
     candidate pool simply starts including keyword matches. `retrieval-benchmarking` is the
     harness used to grade this change; its own requirements do not change. -->

## Impact

**Code**
- `src/data_manager/vectorstore/postgres_vectorstore.py` — parameter binding, SQL-construction helper, fallback logging, BM25 negation, normalization CTE, degenerate guard, docstring.
- `src/cli/templates/base-config.yaml` — comment recording that weights apply to normalized components; values unchanged.
- `docs/` — hybrid-search sign and normalization contract.

**Tests**
- `tests/unit/test_postgres_vectorstore.py` — generated-SQL and parameter-order assertions; fallback-warning assertion; six fixtures corrected to the real negative BM25 convention.
- New integration tests against real PostgreSQL + `pg_textsearch`, skipping cleanly where unavailable without silently hiding the suite.
- `tests/unit/test_hierarchical_retriever.py`, `tests/unit/test_retriever_factory.py` — audited for the same wrong-sign assumption.

**Behavior / operations**
- Retrieval ranking changes for every query on the default path: BM25 begins contributing for the first time. Graded through `archi evaluate` (RAGAS `context_precision` / `context_recall` + SOURCES hit-rate) against the goldenset bank, reported before vs. after.
- Query-time only — **no re-ingest, re-embed, or schema migration**.
- Requires a dev redeploy to take effect (the container runs a non-editable `pip install .`).
- Verified healthy and deliberately untouched: hierarchical parent expansion (5450 chunks carry `parent_id`, 4414 parent rows) and the agent tool's tuple coercion (`tools/retriever.py:17-28`).

**Dependencies**
- None added. `argilla` and `ragas` stay out of `pyproject.toml` / `requirements-base.txt`.

**Deliberately out of scope** — each needs its own issue, and folding any of them in would make the PR unreviewable:
- Weight-default reconciliation: three distinct pairs across ten sites. Mechanical; separate PR.
- The index-scan restructure (`<@>` in a SELECT list forces a sequential scan).
- Write-only `dynamic_config` retrieval knobs — read by nothing in the retrieval path.
- The duplicate, incompatible `static_config` definition (`init.sql:102` vs `config_service.py:195-216`).
- `HybridRetriever` re-raising the missing-index `RuntimeError` instead of degrading.

**Tracking**
- Closes #205. Upstream reporting and the eventual upstream port are #206. Supersedes the local-only proposal at `f0cbcc5a`, which was written before the binding defect was found.
