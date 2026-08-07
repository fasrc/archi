## Context

The benchmarking harness (`src/bin/service_benchmark.py`) runs inside the benchmarks container. `Benchmarker.get_all_configs` walks a configs directory and `load_new_configuration` pops and runs each config in turn; for each, `RAGAS` mode computes four metric means and stores them on `total_results` (`aggregate_answer_relevancy`, `aggregate_faithfulness`, `aggregate_context_precision`, `aggregate_context_recall` — service_benchmark.py:850-857). `ResultHandler.handle_results` appends a record per config to `ResultHandler.results`, each carrying `total_results`, the full `configuration`, and `configuration_file` (service_benchmark.py:114-128). After the loop, when `len(ResultHandler.results) >= 2`, the harness auto-generates pairwise `ab_comparisons` (service_benchmark.py:868-887). Finally `ResultHandler.dump` writes one JSON to `out_dir` with `benchmarking_results`, `metadata`, and optional `ab_comparison`/`ab_comparisons` (service_benchmark.py:166-181).

The SUT prompt is selected entirely by `services.benchmarking.agent_md_file`; `load_agent_spec` loads it and `archi(...)` builds the chain (service_benchmark.py:360-400). So running N prompts is, mechanically, N configs that differ only in that one field.

The variant label used in reporting is read from `services.benchmarking.name` (`dump_ab_comparison`, service_benchmark.py:255-263), which falls back to `config_<idx>`. The shipped `config/benchmarking/ragas.yaml` sets only a top-level `name`, so today a multi-config run would label variants `config_0`, `config_1`, … — useless for a leaderboard. Per-variant naming must therefore be set under `services.benchmarking`.

`docs/docs/notes_response_tuning.md` settled Decision 3 (a ~5-section simplified prompt) and left Q5 (replacement vs A/B) open; this change is the measurement instrument that settles Q5 across the whole prompt field, not just one pair.

## Goals / Non-Goals

**Goals:**
- Run N prompt variants through the existing harness with model, queries, retriever, and RAGAS judge held fixed — the prompt file is the only variable.
- Produce a ranked **leaderboard**: one row per variant (name + prompt file), the four mean RAGAS metrics, ranked by a configurable primary metric.
- Make starting a sweep one command: generate configs from a manifest, then `archi evaluate --config-dir <sweep_dir>`.
- Keep the leaderboard independent of the pairwise A/B plumbing (the "leaderboard only" decision).
- Record shared run context (model, judge, queries, `corpus_snapshot_id`) once so a sweep result is self-describing and reproducible.
- Backward-compatible: single-config runs emit no leaderboard; existing pairwise output is unchanged.

**Non-Goals:**
- Replacing or modifying pairwise `ab_comparisons` (kept as-is, runs alongside).
- Sweeping the model, the queries, the retriever, or the RAGAS judge prompt — those are fixed by definition of a *prompt* sweep.
- Statistical significance testing of leaderboard gaps (means only for v1; significance is a follow-up).
- Auto-promoting the winning prompt into the production chatbot config.
- Feature 1 (per-source relevance) and Feature 2 (inline attribution) from the tuning notes — separate changes.

## Decisions

### D1. Leaderboard reads per-config aggregates, not pairwise results

The aggregator iterates `ResultHandler.results` and reads `record["total_results"]["aggregate_*"]` plus `record["configuration"]["services"]["benchmarking"]` for the variant name and `agent_md_file`. It never touches `pair_ab_results`/`ab_comparisons`. This honors the "leaderboard only (new aggregator)" decision: ranking N variants by mean metric is an O(N) reduction over data the run loop already produced, whereas pairwise is O(N²) and answers a different question (head-to-head win counts).

**Alternatives considered:** (a) Derive the leaderboard from `ab_comparisons` mean_scores — couples it to the O(N²) pairing, and those means are recomputed per pair rather than once per variant. (b) Re-run RAGAS for aggregation — wasteful; the means already exist on `total_results`.

### D2. New `ResultHandler.build_leaderboard()` + `leaderboard` dump key

Add a static `build_leaderboard(primary_metric: str) -> Dict[str, Any]` that returns `{shared_context, primary_metric, rows: [...]}` and stores it on `ResultHandler.leaderboard`. `dump()` gains `if ResultHandler.leaderboard: output["leaderboard"] = ResultHandler.leaderboard`, mirroring the existing `ab_comparison(s)` pattern exactly (service_benchmark.py:176-179). Invoke it in the run-tail right after the pairwise block, gated on `len(ResultHandler.results) >= 2` (a one-variant "sweep" is just a normal run — no ranking to do).

**Alternatives considered:** a separate post-processing script reading the dump JSON — would duplicate the metric-key knowledge and run out-of-band; building it in-process reuses the live `ResultHandler.results` and the same dump file.

### D3. Each leaderboard row is keyed by `(name, agent_md_file)` and self-labels its prompt

A row is `{name, agent_md_file, metrics: {answer_relevancy, faithfulness, context_precision, context_recall}, primary_score, rank}`. `name` comes from `services.benchmarking.name`; if absent it falls back to the `agent_md_file` stem (not `config_<idx>`), so even un-named configs read sensibly. The prompt file path is always carried so a row is traceable to the exact prompt without cross-referencing.

### D4. Ranking is by a configurable `primary_metric`, default `faithfulness`

Sort rows descending by `metrics[primary_metric]`; assign dense ranks (ties share a rank). Default `faithfulness` because for a "never guess" support bot, grounding is the load-bearing property (notes_response_tuning §4 keeps the Grounding Rule as the spine). The full four-metric vector is always present in every row, so a reader can re-rank by any metric without re-running. `primary_metric` is read from the manifest (D6) with a code default.

**Alternatives considered:** a single composite score (weighted sum of the four) — hides trade-offs and bakes in weights we have not validated; better to rank by one honest metric and show the rest.

### D5. Missing / NaN metrics are surfaced, never silently zeroed

If a variant's run produced no value for a metric (key absent, or NaN from RAGAS), the row stores `None` for that metric and is marked `"incomplete": true`. Incomplete variants sort *last* regardless of primary metric (a prompt that failed to score is not a winner), and the condition is logged. This mirrors the existing aggregate code's NaN-guarding in `dump_ab_comparison` (service_benchmark.py:288-294) rather than the run loop's bare `.mean()`.

### D6. Sweep configs are generated from a manifest by a standalone script

`scripts/benchmarking/generate_prompt_sweep.py` reads a manifest (`base_config`, `prompts: [paths]`, optional `primary_metric`, optional `out_dir`), loads the base config once, and for each prompt writes a rendered config into the sweep directory with `services.benchmarking.agent_md_file` set to that prompt and `services.benchmarking.name` set to the prompt stem. Everything else is copied verbatim from the base, so model/queries/judge are provably identical across variants. The script prints the `archi evaluate --config-dir <sweep_dir>` line to run next.

**Alternatives considered:** (a) a new `archi evaluate --sweep` flag that expands prompts internally — larger CLI surface and entangles generation with execution; a generator that emits standard configs keeps the existing `--config-dir` path as the single execution entrypoint. (b) Hand-authoring N YAMLs — error-prone; the whole point is that only one field may differ, which a generator guarantees and humans don't.

### D7. Shared context block proves the sweep was apples-to-apples

`build_leaderboard` cross-checks that every config shares the same `model`, `provider`, `evaluator_model`, and `queries_path`, and records them once in `shared_context` alongside `corpus_snapshot_id` (from `ResultHandler.get_corpus_snapshot_id()`). If a mismatch is detected (someone hand-edited a generated config), it is recorded in `shared_context.warnings` and logged — the leaderboard still renders, but the caveat travels with the data.

## Risks / Trade-offs

- **Means without significance.** A 0.02 faithfulness gap on a small query set may be noise. Mitigation: the full per-question results remain in `benchmarking_results`; significance testing is a named follow-up, and the leaderboard logs the query count so a reader sees the sample size.
- **Generated-config drift.** If someone edits a generated config by hand, the sweep is no longer apples-to-apples. Mitigation: D7's shared-context cross-check surfaces the drift as a warning rather than silently ranking incomparable runs.
- **Pairwise still runs.** With N variants the harness also computes O(N²) `ab_comparisons`, which is wasted work when only the leaderboard is wanted. Accepted for this change (the decision was to *not* modify pairwise); a future flag could skip pairing when a sweep manifest is in play.

## Migration / Rollout

No migration. The feature is inert until a sweep manifest is created and configs are generated. Existing single-config and multi-config runs are unchanged except for the additive `leaderboard` key, which appears only when 2+ configs run.
