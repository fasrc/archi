## Why

We have four hand-authored FASRC Cannon support prompts (`fasrc-cannon-v1-strict` → `v4-linked`) plus the simplified ~5-section candidate sketched in `docs/docs/notes_response_tuning.md` §4, and no objective way to pick a winner. Decision 3 in those notes framed this as an "A/B track," but A/B only answers "is B better than A" — it does not rank a field of candidates, and the open question Q5 ("replacement or A/B?") cannot be settled by eyeballing one pair at a time.

The RAGAS+Argilla harness built in the benchmarking work (Phases 2–7) already runs *any* prompt as the system-under-test: `services.benchmarking.agent_md_file` selects the prompt, the harness scores it on `answer_relevancy`, `faithfulness`, `context_precision`, and `context_recall`, and `Benchmarker` already walks a configs *directory* and runs each config sequentially. What is missing is (a) a frictionless way to point the harness at N prompt variants that differ *only* in the prompt file, and (b) an aggregate **leaderboard** that ranks those variants by mean RAGAS metric so the adoption decision is data-driven.

This change adds exactly that, holding model, queries, retriever, and the RAGAS judge fixed so the only moving part is the prompt.

## What Changes

- **Sweep config generation.** A generator takes one base benchmarking config plus a list of prompt files (a sweep manifest) and writes N rendered configs into a sweep directory, each identical except for `services.benchmarking.agent_md_file` and a distinct `services.benchmarking.name` derived from the prompt's filename stem. The existing `archi evaluate --config-dir <sweep_dir>` then runs the whole field with no further code.
- **Leaderboard aggregator.** After all configs run, a new aggregator reads each config's per-run RAGAS aggregates (`total_results.aggregate_*` in `ResultHandler.results`) and emits a ranked leaderboard: one row per variant (name + prompt file), the four mean RAGAS metrics, and a configurable primary-metric ranking. It is emitted as a new `leaderboard` section in the existing dump JSON and as a human-readable table in the log/report.
- **Independent of pairwise A/B.** The leaderboard reads the per-config aggregates directly and does not depend on `pair_ab_results` / `dump_ab_comparison` / `ab_comparisons`. Those remain untouched and continue to behave as today.
- **Reproducibility carried through.** The leaderboard records the shared run context (model, judge model, queries file, `corpus_snapshot_id`) once, so a sweep result is self-describing.

## Capabilities

### New Capabilities
- `ragas-prompt-sweep`: Run N agent-prompt variants through the existing RAGAS harness with everything but the prompt held fixed, and rank them on a leaderboard by mean RAGAS metric. Covers sweep-config generation from a manifest and the leaderboard aggregation/emission.

### Modified Capabilities

None. The existing pairwise A/B comparison, single-config evaluation, and Argilla export paths are unaffected.

## Impact

- **Code**: `src/bin/service_benchmark.py` (new leaderboard aggregator on `ResultHandler`, invoked after the run loop; new `leaderboard` key in `dump()`). New `scripts/benchmarking/generate_prompt_sweep.py` (manifest → N rendered configs). No change to `Benchmarker.load_new_configuration`, `pair_ab_results`, or `dump_ab_comparison`.
- **Config**: New optional manifest file (e.g. `config/benchmarking/prompt_sweep.yaml`) listing the base config and the prompt files to sweep, plus an optional `primary_metric` for ranking. Absence of a manifest changes nothing — the harness behaves exactly as today.
- **Tests**: New unit tests for the leaderboard aggregator (ranking, tie handling, missing-metric handling) and for sweep-config generation (one config per prompt, only `agent_md_file`/`name` differ). No changes to existing tests expected.
- **Docs**: `docs/docs/benchmarking.md` gains a "Prompt sweep" section. The settled decisions in `docs/docs/notes_response_tuning.md` (Decision 3, Q5) are referenced as the motivation.
- **Behavior for existing runs**: Backward-compatible. A normal `archi evaluate -c ragas.yaml` (single config) emits no leaderboard; a multi-config run still produces pairwise `ab_comparisons` exactly as before, now *plus* a leaderboard.
- **Not in scope**: Feature 1 per-source relevance plumbing (`docs/docs/feature1_relevance_plan.md` — separate change), Feature 2 inline attribution, sweeping the RAGAS judge prompt or the model, statistical significance testing of leaderboard gaps, and any automatic promotion of the winning prompt to production config.
