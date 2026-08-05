## 1. Capture the baseline and make the path observable

- [ ] 1.1 Re-run the reproduction against `postgres-dev` and record: collection value and null-count, rows from the mis-bound `WHERE`, rows from the correctly-bound `WHERE`, and the BM25 range. Expect `default_collection_with_HuggingFaceEmbeddings` non-null on 5450/5450, **0** vs **5450** rows, and `−14.4727 … 0.0000`.
- [ ] 1.2 Capture `EXPLAIN (ANALYZE, BUFFERS)` for the current statement and note that `idx_chunks_bm25` is unused (sequential scan).
- [ ] 1.3 Record the goldenset baseline for current `origin/dev` behavior — RAGAS `context_precision`, `context_recall`, SOURCES hit-rate. This is the semantic-only baseline every later option must beat or match; without it the blocking gate in group 6 cannot be evaluated.
- [ ] 1.4 Add a warning at the empty-result fallback (`postgres_vectorstore.py:513-516`) carrying **structured fields** — fallback reason, collection, requested `k`, and a request/trace id — and **not** the raw query text, which may contain personal or confidential content and would otherwise be copied into centralized logs with broader access and retention than the conversation store. Test that it fires on the zero-row path, does not fire when rows are returned, asserts on **field values rather than message prose**, and that a distinctive query string never appears in the record.

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
- [ ] 4.5 Add collection-metadata reproduction cases to the same integration suite: all-non-null (the measured dev case → 0 rows → fallback), all-null, and **mixed** (only the null subset passes → hybrid runs over a silent corpus subset). The mixed case does not fall back, so it must be pinned by a test; it is the variant with no observable signal.
- [ ] 4.6 **Make database execution blocking, not merely reported.** Provision a PostgreSQL + `pg_textsearch` service for CI (or, if that is not achievable in this change, a recorded pre-merge run whose output is pasted into the PR). Add a check that **fails when the named invariant tests did not all execute** — a wholly- or partly-skipped suite must not report success. Reporting a skip while passing recreates the exact gap that let an unexecuted-SQL defect ship for ~6 months.
- [ ] 4.7 **Give each scoring invariant its own named database-executed test**, each reporting executed/passed individually: parameter correspondence, BM25 orientation, normalization applied, normalization placed before the row limit, zero-range degenerate case, `NULL` placement. An aggregate executed-count must not be able to discharge coverage — one happy-path DB test would otherwise satisfy the gate for all six.
- [ ] 4.8 **Mutation-verify each invariant test individually.** Revert each correction one at a time — parameter order, negation, normalization, its pre-`LIMIT` placement, the zero-range guard — and confirm that the named test *for that specific invariant* fails. Confirming only that the suite fails without `pg_textsearch` tests the counter, not the coverage.

## 5. Document the contract

- [ ] 5.1 Update the `hybrid_search` docstring (`:406-427`): each component is oriented higher-is-better; `<@>` emits negative scores and is negated; both components are min-max normalized to `0..1` before weighting; `combined_score` is relative to the query's candidate set and **not** comparable across queries.
- [ ] 5.2 Update the hybrid-search description in `docs/` with the same contract.
- [ ] 5.3 Add a note at `base-config.yaml:240-243` that the weights apply to normalized components and should sum to `1.0`.

## 6. Grade it — a deterministic gate, not a judgement call

Fix the run conditions before measuring anything, or the comparison is not reproducible and the gate is waivable by interpretation.

- [ ] 6.1 **Pre-register the inputs before measuring anything.** Use the **entire** goldenset bank at a pinned git SHA — not a subset, and not a bank chosen after seeing any result — plus a single pinned `corpus_snapshot_id`, embedding model, judge LLM with version and `temperature=0` where supported, `k` / `candidate_pool_size`, and the weight pair. Record this manifest in the change **before** running any arm. Selecting or trimming the bank or snapshot after observing results voids the run. Any arm executed under a different manifest is void, not merely noisy.
- [ ] 6.2 **Use paired per-question comparison, and let noise stop the gate rather than widen it.** Score every arm on the *same* questions and compare **per question** against baseline, not just arm means — pairing cancels most judge variance, which is the dominant noise source, without needing a variance estimate from a handful of runs. Run the baseline **3 times** only as a noise *check*, not to derive a tolerance: if any metric's mean varies by more than `0.02` absolute across those three runs, the bank or judge is too unstable to gate on — **stop and report that**, do not proceed to treatment arms and do not widen any tolerance to compensate.

  This deliberately replaces an earlier `margin = max(2·stdev, 0.01)` rule, which was backwards: it made a noisier judge produce a *wider* tolerance, so worse measurement would have bought more permission to regress. Noise must reduce confidence, never increase allowance.
- [ ] 6.3 Measure **four** arms, 3 runs each, under the pinned inputs — reporting per-metric mean and stdev for `context_precision`, `context_recall`, and SOURCES hit-rate:
  - **A. baseline** — current `origin/dev` (semantic-only in practice)
  - **B. bind-only** — binding fixed, sign and scale defects left live. This arm exists to empirically settle the design's central bundling claim; the deduction predicts it is the worst arm.
  - **C. bind + sign + min-max normalization**
  - **D. bind + sign + RRF**
- [ ] 6.4 Emit a machine-readable artifact and commit it with the change, so the gate is re-checkable rather than a prose claim in a PR body. JSON: the pinned manifest from 6.1, the 6.2 noise-check figures, and per arm → per metric → mean, plus the paired per-question tallies (improved / unchanged / regressed) against baseline.
- [ ] 6.5 **Apply this selection rule literally.**
  1. An arm **passes** only if, for **every** one of the three metrics, its mean is `>=` baseline's mean **and** the number of questions it regresses does not exceed the number it improves. There is no variance-derived margin to inflate: an arm that is worse on any metric fails, full stop.
  2. **Pareto-safe selection.** Only a passing arm may ship. Among passing arms prefer the highest `context_recall`, but an arm MUST NOT be selected if it regresses any other metric relative to baseline — a trivial recall gain does not buy a precision or hit-rate loss. If the recall-preferred arm is not Pareto-safe, pick the arm that regresses nothing.
  3. **If the arm you intend to ship does not itself pass, do not ship it** — even if another arm passes, even if the shortfall looks small. There is no "default" that survives a regression: min-max is only a presumption *among passing arms*.
  4. If **no** arm passes, ship nothing. Report that activating BM25 is a net loss on this bank and stop.
  5. **Clauses 3 and 4 cannot be waived by anyone, including the operator.** They are the safety stops, and a gate whose stops are waivable is not a gate — an earlier revision of this rule allowed blanket operator sign-off for "any deviation", which silently made the whole thing advisory. The *only* waivable decision is the clause-2 preference among arms that have already passed clause 1, and any such choice needs a quantitative rationale recorded on #205. Shipping a failing arm is not available as an option; if it is genuinely wanted, that is a new proposal with its own justification, not a waiver of this one.
- [ ] 6.6 State each shipped metric as the arm mean against baseline, with the paired tallies. If the winning arm's gain over baseline is smaller than the 6.2 noise-check spread, say plainly that the change is non-inferior but not demonstrably better — do not describe it as an improvement.
- [ ] 6.7 Report arm B's result explicitly, whichever way it falls. If B does **not** measure worse than A, the design's "must ship together" rationale is weakened and the bundling decision must be revisited with the operator rather than carried forward unexamined.
- [ ] 6.8 Audit benchmark and A/B tooling for any consumer treating `combined_score` as an absolute threshold, now that it is query-relative.

## 7. Verify and land

- [ ] 7.1 `pytest tests/unit/test_postgres_vectorstore.py tests/unit/test_hierarchical_retriever.py tests/unit/test_retriever_factory.py -v` green.
- [ ] 7.2 `bash scripts/gate.sh` bare, exit 0, ≥80% diff coverage. Needs the miniforge `archi` env on `PATH`. Never `--no-verify`.
- [ ] 7.2a **Blocking:** the database-executed suite ran with a non-zero executed count (4.6). A green `gate.sh` plus green unit tests is **not** sufficient evidence for this change — those were all green while the defect shipped. Paste the executed-test count into the PR body.
- [ ] 7.2b **Blocking:** the group 6 artifact (6.4) is committed, and the shipped arm passes every metric per the 6.5 rule. Do not open the PR describing the change as verified until both hold.
- [ ] 7.3 Confirm `argilla` / `ragas` did not leak into `pyproject.toml` or `requirements-base.txt`.
- [ ] 7.4 Confirm the hierarchical path is still healthy — 5450 chunks with `parent_id`, 4414 parent rows — so the change did not disturb parent expansion.
- [ ] 7.5 Run `/codex:adversarial-review`; verify each finding against the code before acting, and address them before opening the PR.
- [ ] 7.6 Open the PR against `fasrc/archi:dev` with `Closes #205`, the three measurement sets, the `EXPLAIN` comparison, and an explicit note that binding + negation + normalization ship together because any subset regresses.
- [ ] 7.7 File the deliberately-excluded defects as their own issues: weight-default reconciliation, index-scan restructure, write-only `dynamic_config` knobs, duplicate `static_config` definition, `HybridRetriever` re-raise.
- [ ] 7.8 Redeploy dev only with explicit operator approval (`archi-dev-deploy-verify`). Acceptance is container logs showing hybrid candidate generation **without** the fallback warning, plus a keyword-heavy live query returning keyword-relevant sources in the persisted response — not an HTTP 200.
- [ ] 7.8a **Make the rollback real, not just a code revert.** Stamp persisted retrieval outputs and the group-6 benchmark artifact with a ranking-behavior identifier so rows can be attributed to a ranking after the fact; treat the redeploy as a mixed-version window and take no baseline inside it; check whether any response cache can serve pre-revert rankings afterwards. Write down the revert procedure including the marking steps — annotate the benchmark artifact as describing an un-deployed treatment, and flag goldenset baselines and A/B rows captured while it was live. A `git revert` alone leaves treatment-ranked outputs indistinguishable from baseline ones.
- [ ] 7.9 Update #60 (any retrieval baseline predating this fix measured a hybrid search that was not running) and #206 (the fix is ready to port upstream).
