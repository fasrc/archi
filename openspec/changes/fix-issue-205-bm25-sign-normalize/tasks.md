## 1. Reproduce and pin the defect (red first)

- [ ] 1.1 Re-run the live reproduction SQL against `postgres-dev` and record the current numbers, so the "after" comparison has a captured baseline: BM25 range, count of `bm25_score < 0` vs `= 0`, and the top-5 by `combined_score DESC`. Expected today: range `−14.4727…0.0000`, 1463 vs 3987, top-5 all `bm25_score = 0.0000`.
- [ ] 1.2 Capture `EXPLAIN (ANALYZE, BUFFERS)` for the current `hybrid_search` query and note whether `idx_chunks_bm25` is used (expected: no — sequential scan, per the `<@>`-in-SELECT anti-pattern).
- [ ] 1.3 Add a failing unit test in `tests/unit/test_postgres_vectorstore.py`: a candidate with a strong keyword match (large-magnitude **negative** raw `<@>` value) must outrank a candidate with `bm25_score = 0` when semantic scores are comparable. Run it and **watch it fail** on `origin/dev` @ `9144918` before writing any fix.
- [ ] 1.4 Add a failing unit test asserting every returned `combined_score` lies within `0..1` when `semantic_weight + bm25_weight == 1.0`.
- [ ] 1.5 Add a failing unit test for the degenerate case: every candidate carrying an identical BM25 score (including all-zero, the no-term-match case) must not raise and must fall back to semantic-only ordering.
- [ ] 1.6 Add a failing unit test asserting ordering is invariant to BM25 magnitude — two candidate sets with the same relative BM25 order but magnitudes differing by a constant factor must produce the same ranking.

## 2. Fix the scoring expression

- [ ] 2.1 Negate the `<@>` term in the `bm25_score_expr` at `postgres_vectorstore.py:475-477` so higher means a better match, matching the existing `ORDER BY combined_score DESC`.
- [ ] 2.2 Min-max normalize **both** components to `0..1` inside the `scored` CTE using `min(...) OVER ()` / `max(...) OVER ()`, before the `LIMIT`. Normalize the semantic half too — `1.0 - distance` is not bounded `0..1` for the configurable `l2` or `inner_product` metrics (`postgres_vectorstore.py:111-121`), only approximately so for `cosine`.
- [ ] 2.3 Guard the zero-range case with `NULLIF(max - min, 0)` wrapped in `COALESCE(..., 0)`, and keep the existing `COALESCE` on the raw BM25 term so a SQL `NULL` maps to weakest-keyword-evidence rather than nulling `combined_score`.
- [ ] 2.4 Run the tests from group 1 and confirm they now pass — no other production code should need to change.
- [ ] 2.5 Re-capture `EXPLAIN (ANALYZE, BUFFERS)` and confirm the added window functions introduce no extra scan versus the 1.2 baseline. If cost regresses materially, record it in the PR body rather than silently accepting it.

## 3. Correct the test fixtures that hid the bug

- [ ] 3.1 Change every mocked `bm25_score` in `tests/unit/test_postgres_vectorstore.py` (lines 269, 314, 365, 377, 422, 463) from positive values to the backend's real negative-or-zero convention. Expect some of these to go red — that is the point.
- [ ] 3.2 Triage each newly-failing assertion individually: a test that only passed under the inverted sign was asserting the bug, so fix the expectation rather than restoring the old fixture value.
- [ ] 3.3 Audit `tests/unit/test_hierarchical_retriever.py` and `tests/unit/test_retriever_factory.py` for the same wrong-sign assumption in their mocks and correct them.
- [ ] 3.4 Add the guard test required by the spec: removing the negation from the scoring expression must fail at least one test. Verify by temporarily reverting 2.1 and confirming red.

## 4. Document the contract

- [ ] 4.1 Update the `hybrid_search` docstring (`postgres_vectorstore.py:406-427`) to state that each component score is oriented higher-is-better and min-max normalized to `0..1` before weighting, that `pg_textsearch` `<@>` emits negative scores and is therefore negated, and that the returned `combined_score` is **relative to the query's candidate set and not comparable across queries**.
- [ ] 4.2 Update the hybrid-search description in `docs/` with the same sign and normalization contract, and note the weights apply to normalized components.
- [ ] 4.3 Add a comment at `base-config.yaml:240-243` recording that the weights apply to normalized `0..1` components and should sum to `1.0`.

## 5. One source of truth for the weight defaults (separate commit; land as PR 2)

- [ ] 5.1 Define the default pair once in the vectorstore layer (e.g. `DEFAULT_SEMANTIC_WEIGHT = 0.4`, `DEFAULT_BM25_WEIGHT = 0.6`), matching the live `dynamic_config` values so no behavior changes. The vectorstore must not import from `retrievers/`.
- [ ] 5.2 Point the `hybrid_search` signature defaults (`postgres_vectorstore.py:411-412`, currently 0.7/0.3) at the constant.
- [ ] 5.3 Point the `HybridRetriever` dataclass and `__init__` defaults (`hybrid_retriever.py:33-34`, `:40-41`, currently 0.5/0.5) at the constant.
- [ ] 5.4 Point the factory config fallbacks (`factory.py:50-51`) at the constant.
- [ ] 5.5 Point the pipeline fallbacks at the constant: `qa.py:79` and `cms_comp_ops_agent.py:307`.
- [ ] 5.6 Align the `dynamic_config` column defaults at `init.sql:168-169` (currently 0.3/0.7) with the constant. Fresh deployments only — confirm no migration is needed because the live row already stores 0.60/0.40 explicitly.
- [ ] 5.7 Align the `config_service.py` weight fallbacks at `:89`, `:973`, `:1058` (currently 0.3). **Do not** touch the `static_config` DDL at `:195-216` — see 5.8.
- [ ] 5.8 File a separate issue for the `static_config` table-name collision: `init.sql:102` and `config_service.py:195-216` define incompatible tables of the same name, so config_service's `CREATE TABLE IF NOT EXISTS` is a silent no-op and its reads at `:606` / `:757` would `KeyError` against a real deployment (confirmed: the live dev `static_config` has none of those columns; the weights live in `dynamic_config`). Out of scope here — do not conflate it with the sign fix.
- [ ] 5.9 Verify no competing literal pair remains: grep the ten sites listed in design.md and confirm each reads from the constant.

## 6. Verify

- [ ] 6.1 Run `pytest tests/unit/test_postgres_vectorstore.py tests/unit/test_hierarchical_retriever.py tests/unit/test_retriever_factory.py -v` green.
- [ ] 6.2 Run `bash scripts/gate.sh` bare (needs the miniforge `archi` env on `PATH`) and confirm exit 0 with ≥80% diff coverage on changed lines. Never bypass with `--no-verify`.
- [ ] 6.3 Confirm `argilla` / `ragas` did not leak into `pyproject.toml` or `requirements-base.txt`.
- [ ] 6.4 Re-run the 1.1 reproduction SQL with the shipped expression and confirm the `−14.47` keyword-matching chunk now outranks the `0.0000` zero-overlap chunks.
- [ ] 6.5 Grade through the existing harness: `archi evaluate` against the goldenset bank, capturing RAGAS `context_precision` / `context_recall` and SOURCES hit-rate before vs. after. Report the numbers in the PR body **whatever they show** — if retrieval metrics do not move, say so rather than assuming the fix helped.
- [ ] 6.6 Audit benchmark/A-B tooling for any consumer treating `combined_score` as an absolute threshold, now that it is query-relative.

## 7. Review and land

- [ ] 7.1 Run `/codex:adversarial-review` and address findings before opening the PR; verify each finding against the code rather than accepting it wholesale.
- [ ] 7.2 Open the fix PR against `fasrc/archi:dev` (`gh pr create --repo fasrc/archi --base dev`) with `Closes #205`, the before/after measurements, and the `EXPLAIN` comparison. No `Co-Authored-By` trailers.
- [ ] 7.3 Keep the weight-reconciliation work (group 5) as its own PR so mechanical churn does not mix with the behavior fix.
- [ ] 7.4 Comment on #60 linking the merged fix, and note that any baseline captured before it measured a broken retriever.
- [ ] 7.5 Redeploy dev only with explicit operator approval (`archi-dev-deploy-verify`: `redeploy.sh` + live chat HTTP-200 smoke test), then re-confirm 6.4 against the running deployment.
