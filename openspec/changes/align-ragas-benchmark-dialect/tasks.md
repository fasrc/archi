## 0. Prerequisite — sequence behind PR #92

- [ ] 0.1 Wait for PR #92 (`fix/benchmark-per-question-resilience`, change `harden-benchmark-and-agent-resilience`) to merge to `dev`; it rewrites the `service_benchmark.py` run loop into a thin call site over `src/utils/benchmark_resilience.py`
- [ ] 0.2 Branch this change from post-#92 `origin/dev`; re-anchor the line references below against the merged loop structure (they will have moved)
- [ ] 0.3 Confirm the ownership split: #92 owns run-status isolation + keyed per-question attribution (this change adds NO positional-join replacement); this change DOES own per-metric eligibility + per-metric denominators, which #92's whole-column `build_ragas_aggregates` structurally cannot express

## 1. Shared normalizer (TDD)

- [ ] 1.1 Write failing tests: legacy `question→user_input`, `answer→reference` (explicitly NOT `response`), `contexts→retrieved_contexts`; modern records pass through unchanged; extension keys (`sources`, `source_match_field`, `anchor_type`, `notes`) preserved
- [ ] 1.2 Implement the normalizer as pure helpers in `src/utils/benchmark_resilience.py` (extend #92's module — same black-clean, unit-tested seam) or a sibling `benchmark_schema.py` if the resilience module should stay single-purpose
- [ ] 1.3 Add an explicit regression test pinning `answer → reference` (the highest-risk mapping) so it can never silently revert to `answer → response`

## 2. Harness dialect rewrite (on the post-#92 loop)

- [ ] 2.1 Write failing test: bank load normalizes both legacy and modern records; required-field validation is on `user_input`
- [ ] 2.2 Wire normalize-on-read into the bank load path (`QandA.txt` → `queries_to_answers`), re-anchored to the merged loop
- [ ] 2.3 Write failing test: RAGAS scoring input is a `ragas.EvaluationDataset` with modern columns only, extension fields absent
- [ ] 2.4 Replace the raw `datasets.Dataset` + legacy columns with `EvaluationDataset.from_list` keyed `user_input/retrieved_contexts/response/reference`, built from #92's scorable set
- [ ] 2.5 Write failing test: empty-`reference` rows are excluded from `context_precision`/`context_recall`; `answer_relevancy`/`faithfulness` still score them
- [ ] 2.6 Implement per-metric eligibility as a helper alongside #92's `scorable_items`/`is_scorable` (data-emptiness axis composing on top of the status axis): score each metric over its own eligible `EvaluationDataset` and report its `n_scored / n_total` — a mean over real rows, not a skip-NaN mean over the full set

## 3. Data bank migration (schema-only)

- [ ] 3.1 Migrate `examples/benchmarking/fasrc_ragas_queries.json` (`question→user_input`, `answer→reference`; keep `sources`/`source_match_field`/`anchor_type`/`notes`)
- [ ] 3.2 Migrate `snow_ragas_queries_pt1.json`
- [ ] 3.3 Migrate `anchor_questions.json` (schema-only; note stale KB facts are tracked separately, not corrected here)
- [ ] 3.4 Migrate `queries.json`
- [ ] 3.5 Verify each migrated bank loads against the modern contract without a missing-field error

## 4. Docs + config

- [ ] 4.1 Update `fasrc_ragas_queries.README.md` — modern field table + the normalize-on-read (`answer→reference`) note
- [ ] 4.2 Update `docs/docs/benchmarking.md` to the modern contract
- [ ] 4.3 Update the `queries_path` field docs in `base-config.yaml`

## 5. Tests + gate

- [ ] 5.1 Update `tests/unit/test_ragas_evaluator_local_mode.py` to the modern contract
- [ ] 5.2 Update `tests/unit/test_benchmark_ragas_only_match_fields.py`; extend `tests/unit/test_benchmark_resilience.py` for the new eligibility helper
- [ ] 5.3 Update `tests/smoke/ragas_smoke.py`
- [ ] 5.4 Run `bash scripts/gate.sh`; confirm ≥80% diff coverage on changed lines and all tests green
- [ ] 5.5 (Live validation, optional) rebuild the benchmark image and run a RAGAS-mode smoke pass end-to-end

## 6. Cross-repo handoff

- [ ] 6.1 File/track the companion `fasrc/ragas-json-editor` change (preserve extension fields on edit) referencing this shared normalize contract as the seam
