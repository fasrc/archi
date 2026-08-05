## 1. Capture the baseline and make the path observable

- [ ] 1.1 Re-run the reproduction against `postgres-dev` and record: collection value and null-count, rows from the mis-bound `WHERE`, rows from the correctly-bound `WHERE`, and the BM25 range. Expect `default_collection_with_HuggingFaceEmbeddings` non-null on 5450/5450, **0** vs **5450** rows, and `−14.4727 … 0.0000`.
- [ ] 1.2 Capture `EXPLAIN (ANALYZE, BUFFERS)` for the current statement and note that `idx_chunks_bm25` is unused (sequential scan).
- [ ] 1.3 Record the goldenset baseline for current `origin/dev` behavior — RAGAS `context_precision`, `context_recall`, SOURCES hit-rate. This is the semantic-only baseline every later option must beat or match; without it the blocking gate in group 6 cannot be evaluated.
- [ ] 1.4 Add a `logger.warning` at the empty-result fallback (`postgres_vectorstore.py:513-516`) naming the query and `k`, with a test asserting it fires on the zero-row path and does **not** fire when rows are returned.

## 2. Make the parameter binding structural (red first)

- [ ] 2.1 Extract SQL assembly into a helper returning `(sql, params)`, appending each parameter where its fragment is composed rather than concatenating three lists afterwards.
- [ ] 2.2 Write a failing test asserting the query text reaches the `to_bm25query()` placeholder and the collection name reaches the collection-equality placeholder. Run it and **watch it fail** on `origin/dev` @ `9144918`.
- [ ] 2.3 Write a failing test for the same correspondence with a metadata `filter` supplied, so added `WHERE` placeholders do not shift the mapping.
- [ ] 2.4 Fix the binding so both tests pass. Delete the now-redundant `# Params order:` comment rather than updating it — the test is the specification.
- [ ] 2.5 Add a guard test that fails if the parameter sequence is reordered so the collection name reaches the BM25 expression. Verify by temporarily reintroducing the defect.

## 3. Fix orientation and scale (red first)

- [ ] 3.1 Failing test: a keyword-matching chunk (large-magnitude negative raw `<@>`) outranks a zero-overlap chunk with comparable semantic scores.
- [ ] 3.2 Failing test: ordering is invariant when all BM25 magnitudes are multiplied by a constant positive factor.
- [ ] 3.3 Failing test: every returned `combined_score` lies in `0..1` when the weights sum to `1.0`.
- [ ] 3.4 Failing test: an all-equal BM25 set (including all-zero, the no-term-match case) neither raises nor disturbs semantic-only ordering.
- [ ] 3.5 Failing test: `bm25_weight=0` reproduces semantic-only ordering; `semantic_weight=0` reproduces keyword-only ordering.
- [ ] 3.6 Negate the `<@>` term so higher means better.
- [ ] 3.7 Min-max normalize **both** components to `0..1` via `min(...) OVER ()` / `max(...) OVER ()` inside the `scored` CTE, before the `LIMIT`. Normalize the semantic half too — `1.0 - distance` is not bounded `0..1` for `l2` or `inner_product` (`postgres_vectorstore.py:111-121`).
- [ ] 3.8 Guard the zero-range case with `COALESCE((x - lo) / NULLIF(hi - lo, 0), 0)`, keeping the existing `COALESCE` on the raw term so SQL `NULL` maps to weakest-keyword-evidence.
- [ ] 3.9 Add a guard test that fails if the negation is removed. Verify by temporarily reverting 3.6.
- [ ] 3.10 Re-capture `EXPLAIN (ANALYZE, BUFFERS)` and confirm the window functions add no extra scan versus 1.2. Record any material cost change in the PR body rather than accepting it silently.

## 4. Replace the test strategy

- [ ] 4.1 Change all six mocked `bm25_score` fixtures in `tests/unit/test_postgres_vectorstore.py` (lines 269, 314, 365, 377, 422, 463) from positive values to the backend's negative-or-zero convention.
- [ ] 4.2 Triage each newly-failing assertion individually. A test that only passed under the inverted sign was asserting the bug — fix the expectation, do not restore the old fixture value.
- [ ] 4.3 Audit `tests/unit/test_hierarchical_retriever.py` and `tests/unit/test_retriever_factory.py` for the same wrong-sign assumption and correct them.
- [ ] 4.4 Add integration tests that execute the generated statement against real PostgreSQL + `pg_textsearch`, covering negative, zero, all-equal, mixed-`NULL`, and magnitude-scaled score sets.
- [ ] 4.5 Make the extension-unavailable case a reported **skip**, and confirm in CI that the skip does not silently hide the whole integration suite. Determine whether CI has `pg_textsearch` available at all; if not, say so explicitly in the PR rather than leaving the coverage theoretical.

## 5. Document the contract

- [ ] 5.1 Update the `hybrid_search` docstring (`:406-427`): each component is oriented higher-is-better; `<@>` emits negative scores and is negated; both components are min-max normalized to `0..1` before weighting; `combined_score` is relative to the query's candidate set and **not** comparable across queries.
- [ ] 5.2 Update the hybrid-search description in `docs/` with the same contract.
- [ ] 5.3 Add a note at `base-config.yaml:240-243` that the weights apply to normalized components and should sum to `1.0`.

## 6. Grade it — blocking, not advisory

- [ ] 6.1 Measure the goldenset metrics for sign-corrected **min-max**: RAGAS `context_precision`, `context_recall`, SOURCES hit-rate.
- [ ] 6.2 Implement **RRF** behind the same seam and measure it on the same bank, same run conditions.
- [ ] 6.3 Report all three result sets — baseline (1.3), min-max, RRF — in the PR body.
- [ ] 6.4 Ship min-max unless RRF is materially better. **If every option regresses any of the three metrics against baseline, do not ship** — report the finding and stop, because activating BM25 would then be a net loss.
- [ ] 6.5 If the chosen option's gain falls within run-to-run noise, state that explicitly rather than claiming an improvement.
- [ ] 6.6 Audit benchmark and A/B tooling for any consumer treating `combined_score` as an absolute threshold, now that it is query-relative.

## 7. Verify and land

- [ ] 7.1 `pytest tests/unit/test_postgres_vectorstore.py tests/unit/test_hierarchical_retriever.py tests/unit/test_retriever_factory.py -v` green.
- [ ] 7.2 `bash scripts/gate.sh` bare, exit 0, ≥80% diff coverage. Needs the miniforge `archi` env on `PATH`. Never `--no-verify`.
- [ ] 7.3 Confirm `argilla` / `ragas` did not leak into `pyproject.toml` or `requirements-base.txt`.
- [ ] 7.4 Confirm the hierarchical path is still healthy — 5450 chunks with `parent_id`, 4414 parent rows — so the change did not disturb parent expansion.
- [ ] 7.5 Run `/codex:adversarial-review`; verify each finding against the code before acting, and address them before opening the PR.
- [ ] 7.6 Open the PR against `fasrc/archi:dev` with `Closes #205`, the three measurement sets, the `EXPLAIN` comparison, and an explicit note that binding + negation + normalization ship together because any subset regresses.
- [ ] 7.7 File the deliberately-excluded defects as their own issues: weight-default reconciliation, index-scan restructure, write-only `dynamic_config` knobs, duplicate `static_config` definition, `HybridRetriever` re-raise.
- [ ] 7.8 Redeploy dev only with explicit operator approval (`archi-dev-deploy-verify`). Acceptance is container logs showing hybrid candidate generation **without** the fallback warning, plus a keyword-heavy live query returning keyword-relevant sources in the persisted response — not an HTTP 200.
- [ ] 7.9 Update #60 (any retrieval baseline predating this fix measured a hybrid search that was not running) and #206 (the fix is ready to port upstream).
