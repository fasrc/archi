## 0. Prerequisite — sequence behind PR #92

- [x] 0.1 Wait for PR #92 (`fix/benchmark-per-question-resilience`, change `harden-benchmark-and-agent-resilience`) to merge to `dev`; it rewrites the `service_benchmark.py` run loop into a thin call site over `src/utils/benchmark_resilience.py` — merged as `30004cd3`
- [x] 0.2 Branch this change from post-#92 `origin/dev`; re-anchor the line references below against the merged loop structure (they will have moved) — the RAGAS row build moved to `_answer_and_score_question` (dict at ~1264) + collector `_process_config` (~1163); `get_ragas_results` at ~1031; `_merge_anchor_questions` at ~1496
- [x] 0.3 Confirm the ownership split: #92 owns run-status isolation + keyed per-question attribution (this change adds NO positional-join replacement); this change DOES own per-metric eligibility + per-metric denominators, which #92's whole-column `build_ragas_aggregates` structurally cannot express

## 1. Shared normalizer (TDD)

- [x] 1.1 Write failing tests: legacy `question→user_input`, `answer→reference` (explicitly NOT `response`), `contexts→retrieved_contexts`; modern records pass through unchanged; extension keys (`sources`, `source_match_field`, `anchor_type`, `notes`) preserved
- [x] 1.2 Implement the normalizer as pure helpers in a sibling `src/utils/benchmark_schema.py` (kept the resilience module single-purpose; schema module owns dialect + eligibility, both ragas-free)
- [x] 1.3 Add an explicit regression test pinning `answer → reference` (the highest-risk mapping) so it can never silently revert to `answer → response` (`test_answer_maps_to_reference_never_response`)

## 2. Harness dialect rewrite (on the post-#92 loop)

- [x] 2.1 Write failing test: bank load normalizes both legacy and modern records; required-field validation is **mode-specific and separate from metric eligibility** after normalization — `user_input` always; `sources` (+ match fields) additionally for SOURCES. RAGAS mode requires only `user_input` at load: an empty `reference` is **valid input** (a draft row) that per-metric eligibility later excludes from the context metrics (`required_fields_for_modes` tests in test_benchmark_schema.py)
- [x] 2.2 Wire normalize-on-read into the bank load path (`normalize_bank` at `Benchmarker.__init__`), re-anchored to the merged loop
- [x] 2.3 Write failing test: RAGAS scoring input is a `ragas.EvaluationDataset` with modern columns only, extension fields absent (`test_get_ragas_results_builds_modern_dialect_and_scores_per_eligibility`)
- [x] 2.4 Replace the raw `datasets.Dataset` + legacy columns with `EvaluationDataset.from_list` keyed `user_input/retrieved_contexts/response/reference`, built from #92's scorable set
- [x] 2.5 Write failing test: empty-`reference` rows excluded from `context_precision`/`context_recall` while `answer_relevancy`/`faithfulness` still score them; per-metric subset scores attach to the correct question; a zero-eligible-row metric records `n/a` without calling RAGAS
- [x] 2.6 Implement per-metric eligibility as a helper alongside #92's `scorable_items`/`is_scorable` (data-emptiness axis composing on top of the status axis): `score_metrics_per_eligibility` scores each metric over its own eligible `EvaluationDataset` and reports its `n_scored / n_total` — a mean over real rows, not a skip-NaN mean over the full set
- [x] 2.7 Attach each per-metric subset result back by #92's **per-question key** carried through the subset (`scorable_items` keys), never positionally — so excluding a row never shifts other rows' scores (Codex #93 F5)
- [x] 2.8 Guard the empty per-metric subset: when a metric has zero eligible rows, record `n/a` / `0 of n_total` instead of calling RAGAS on an empty `EvaluationDataset` (Codex #93 F6). The all-failed config still routes through #92's config-level `build_ragas_aggregates(None)` sentinel

## 3. Data bank migration (schema-only)

- [x] 3.1 Migrate `examples/benchmarking/fasrc_ragas_queries.json` (`question→user_input`, `answer→reference`; keep `sources`/`source_match_field`/`anchor_type`/`notes`). `should_refuse` rows keep their **non-empty referral** in `reference` — verified (3 rows, all non-empty)
- [x] 3.2 Normalize the **anchor-file load path** before migrating anchors: `_merge_anchor_questions` now `normalize_bank`s the anchor file on read and dedups/appends on `user_input` (was `question`) — migrated anchors are not silently skipped (Codex #93 F1)
- [x] 3.3 Migrate `anchor_questions.json` (schema-only; **paired with 3.2**; stale KB facts tracked separately, not corrected here)
- [x] 3.4 Migrate `queries.json`
- [x] 3.5 `snow_ragas_queries_pt1.json` is **gitignored/operator-local** real ServiceNow data — NOT a tracked migration. Operator handoff: the harness normalizes legacy banks on read (`normalize_bank` at load), so the operator-local bank keeps working unchanged; documented in docs/docs/benchmarking.md ("legacy question/answer banks are normalized on read"). Never commit or require this file
- [x] 3.6 Verify each migrated in-repo bank loads against the modern contract without a missing-field error (idempotent `normalize_bank` + `required_fields_for_modes` check on all 50 records)

## 4. Docs + config

- [x] 4.1 Update `fasrc_ragas_queries.README.md` — modern field table + the normalize-on-read (`answer→reference`) note
- [x] 4.2 Update `docs/docs/benchmarking.md` to the modern contract
- [x] 4.3 Update the `queries_path` field docs in `base-config.yaml` (added a doc comment; the template had none)

## 5. Tests + gate

- [x] 5.1 `tests/unit/test_ragas_evaluator_local_mode.py` — verified: it exercises `get_ragas_llm_evaluator` (judge client resolution), carries no bank-dialect content, so no change is needed for the modern contract
- [x] 5.2 Update `tests/unit/test_benchmark_ragas_only_match_fields.py` (modern `user_input` records); per-metric eligibility helper covered by new `tests/unit/test_benchmark_schema.py` + `tests/unit/test_benchmark_ragas_dialect.py`
- [x] 5.3 Update `tests/smoke/ragas_smoke.py` to `EvaluationDataset.from_list` + modern columns
- [x] 5.4 Run `bash scripts/gate.sh`; ≥80% diff coverage on changed lines (95% cumulative: benchmark_schema 100%, service_benchmark 85.7%) and all tests green
- [ ] 5.5 (Live validation, optional — release/operator step) rebuild the benchmark image (non-editable install) and run a RAGAS-mode smoke pass end-to-end

## 6. Cross-repo handoff

- [ ] 6.1 File/track the companion `fasrc/ragas-json-editor` change (preserve extension fields on edit; fix `ragas.js` import mapping `answer→response`→`answer→reference`) referencing this shared normalize contract as the seam — follow-up in the editor repo (its own `/opsx:propose`)
