## 1. Sweep config generator

- [x] 1.1 Create `scripts/benchmarking/generate_prompt_sweep.py` with a `--manifest` argument pointing at a sweep manifest YAML (keys: `base_config`, `prompts: [paths]`, optional `primary_metric`, optional `out_dir`).
- [x] 1.2 Load the base config once with `yaml.safe_load`. For each prompt path, deep-copy the base, set `services.benchmarking.agent_md_file` to that path and `services.benchmarking.name` to the prompt filename stem, and write the rendered config into the sweep dir (default `bench_out/sweep_configs/`).
- [x] 1.3 Validate every prompt path exists *before* writing any config; raise naming the first missing path and leave the sweep dir untouched (atomic generation).
- [x] 1.4 Print the next command to run: `archi evaluate --config-dir <sweep_dir> --hostmode ...` and the chosen `primary_metric`.
- [x] 1.5 Write an example manifest at `config/benchmarking/prompt_sweep.yaml` covering the `fasrc-cannon-v1-strict` → `v4-linked` variants, with comments. Use `config/benchmarking/ragas.yaml` as `base_config`.
- [x] 1.6 Run pyright on `generate_prompt_sweep.py`; confirm no new errors against the baseline.

## 2. Leaderboard aggregator on ResultHandler

- [x] 2.1 Add class attribute `leaderboard: Dict[str, Any] = {}` to `ResultHandler` next to `ab_comparisons` (`src/bin/service_benchmark.py` ~line 76).
- [x] 2.2 Add static `ResultHandler.build_leaderboard(primary_metric: str = "faithfulness") -> Dict[str, Any]` that iterates `ResultHandler.results`, reads each `total_results.aggregate_{answer_relevancy,faithfulness,context_precision,context_recall}`, and reads `name` / `agent_md_file` from `configuration.services.benchmarking` (name falls back to the `agent_md_file` stem, never `config_<idx>`).
- [x] 2.3 For each metric, store `None` when the aggregate key is absent or its value is NaN (guard with `math.isnan`, mirroring `dump_ab_comparison` at service_benchmark.py:288-294); mark the row `incomplete: true` if any metric is `None`.
- [x] 2.4 Sort rows: complete rows first by descending `metrics[primary_metric]`, incomplete rows last; assign dense ranks where ties share a rank. Store `primary_score` and `rank` on each row.
- [x] 2.5 Build `shared_context` = `{model, provider, evaluator_model, queries_path, corpus_snapshot_id}` from the configs; cross-check those four fields across all configs and append any mismatch to `shared_context.warnings` (also log it). Pull `corpus_snapshot_id` from `ResultHandler.get_corpus_snapshot_id()`.
- [x] 2.6 Return and store `{shared_context, primary_metric, rows}` on `ResultHandler.leaderboard`.

## 3. Wire the aggregator into the run tail + dump

- [x] 3.1 In the run-tail (after the pairwise `ab_comparisons` block, ~service_benchmark.py:887), call `ResultHandler.build_leaderboard(primary_metric)` gated on `len(ResultHandler.results) >= 2`. Resolve `primary_metric` from `services.benchmarking.primary_metric` with a `"faithfulness"` default.
- [x] 3.2 Log the ranked leaderboard as a readable table (rank, name, primary_score, all four metrics, query count) at INFO.
- [x] 3.3 In `ResultHandler.dump`, add `if ResultHandler.leaderboard: output["leaderboard"] = ResultHandler.leaderboard`, mirroring the `ab_comparison(s)` lines (service_benchmark.py:176-179).
- [x] 3.4 Run pyright on `service_benchmark.py`; confirm no new errors against the baseline.

## 4. Unit tests: leaderboard aggregator

- [x] 4.1 Create `tests/unit/test_prompt_sweep_leaderboard.py`.
- [x] 4.2 Seed `ResultHandler.results` with 3 synthetic config records (distinct `services.benchmarking.name` + `agent_md_file`, distinct `total_results` aggregates); assert `build_leaderboard` returns 3 rows, each with the right name, prompt file, and four metrics.
- [x] 4.3 Assert default ranking is by descending `faithfulness` with `rank` 1 on the highest; assert a configured `primary_metric` re-ranks while all four metric values remain present.
- [x] 4.4 Assert two equal primary-metric values share a `rank` (tie handling).
- [x] 4.5 Assert a NaN / missing aggregate yields `None` for that metric, marks the row `incomplete: true`, and sorts it after all complete rows.
- [x] 4.6 Assert a row with no `services.benchmarking.name` falls back to the `agent_md_file` stem (never `config_<idx>`).
- [x] 4.7 Assert `shared_context` captures model/provider/judge/queries/corpus_snapshot_id once for a uniform sweep, and records a warning when one config's `model` differs.
- [x] 4.8 Reset `ResultHandler.results`/`leaderboard` in fixtures so tests don't leak state across cases (class-level mutable state).

## 5. Unit tests: sweep config generation

- [x] 5.1 Create `tests/unit/test_generate_prompt_sweep.py`.
- [x] 5.2 Given a base config + 3 temp prompt files, assert exactly 3 configs are written and each differs from the base only in `services.benchmarking.agent_md_file` and `.name` (deep-compare all other keys equal).
- [x] 5.3 Assert `name` equals the prompt filename stem for each generated config.
- [x] 5.4 Assert a manifest referencing a non-existent prompt raises naming the missing path and writes zero configs (atomicity).

## 6. Dump-level test: leaderboard emission gating

- [x] 6.1 Add a test that `dump()` includes a `leaderboard` key only when `ResultHandler.leaderboard` is populated (multi-config), and omits it for a single-config run (assert single-config dump output is otherwise unchanged).

## 7. Docs

- [x] 7.1 Add a "Prompt sweep" section to `docs/docs/benchmarking.md`: manifest format, `generate_prompt_sweep.py` usage, `archi evaluate --config-dir`, and how to read the `leaderboard` block (rows, ranking, `shared_context`, incomplete rows).
- [x] 7.2 Cross-link the motivation: reference Decision 3 / Q5 in `docs/docs/notes_response_tuning.md`. Note explicitly that the model and RAGAS judge are held fixed and only `agent_md_file` varies.

## 8. Verification

- [x] 8.1 Run the full unit test suite; confirm existing tests still pass and the new tests in (4), (5), (6) pass. *(Result: 263 passed, 17 new tests pass; 1 failure `test_ingestion_pipeline_isolation::test_loader_returns_content` is the same pre-existing, unrelated failure noted in the prior phase. No regressions.)*
- [x] 8.2 Run pyright on `service_benchmark.py` and `scripts/benchmarking/generate_prompt_sweep.py` in one invocation; confirm zero new diagnostics vs baseline. *(Result: the only `service_benchmark.py` diagnostics are pre-existing — lines 462/622-656/814 and the `langchain_ollama` import; none reference the added `build_leaderboard`/`leaderboard`/`LEADERBOARD_METRICS` symbols. `generate_prompt_sweep.py` clean.)*
- [x] 8.3 Generate a real sweep from `config/benchmarking/prompt_sweep.yaml` (the three archived `fasrc-cannon` variants), run `archi evaluate --config-dir <sweep_dir> --hostmode` against the local Qwen SUT + HUIT Bedrock judge, and confirm the dump JSON contains a `leaderboard` with ranked rows and a populated `shared_context` (matching model/judge, empty warnings). *(Done 2026-06-10: `bench_out/benchmarking-bench-sweep-20260610_015120.json` — 3 ranked rows, `shared_context` populated, `warnings: []`. Making `--config-dir` actually run end-to-end required fixing four multi-config plumbing bugs — see the `fix-config-dir-benchmarking-variation` change. Ran against the idle 3.5 SUT; some RAGAS judge calls hit the 180s timeout, so the metric values are machinery-proof, not a prompt decision.)*
- [x] 8.4 Confirm a single-config run still produces no `leaderboard` key (backward compatibility) and that pairwise `ab_comparisons` are unchanged on the multi-config run. *(Covered at unit level: `test_prompt_sweep_dump_gating::test_no_leaderboard_for_single_config` proves the gating; leaderboard is built independently of the untouched pairwise block. Live confirmation folds into 8.3.)*
