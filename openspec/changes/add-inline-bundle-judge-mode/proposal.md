## Why

Choosing an open-weights RAGAS judge for the Slurm offload (fasrc/archi#737, step 5.3) needs the **same answers** scored by the inline Sonnet judge and by each candidate judge. Today an inline run keeps only the first 300 characters of each retrieved passage (`sources_trunc_content`), so its judge input has to be rebuilt afterwards from the benchmark stack's Postgres volume by `parent_id`; that recovery drops every row with a parent-less source (120 of 545 rows across the five October 2026 golden-set runs) and depends on a volume that is routinely pruned. A deferred run keeps the full input but makes no Sonnet call, so it gives nothing to compare against. The benchmark needs a mode that does both, now, so that every golden-set run from here on is scorable offline without any rebuild.

## What Changes

- A third `ragas_settings.judge_mode` value, `inline+bundle`: the run writes each arm's judge bundle (`rows.jsonl` with the full `user_input`, `retrieved_contexts`, `response`, `reference`; `manifest.json`; `BUNDLE_COMPLETE`) **before** it builds the judge, then scores inline exactly as `inline` does. The arm record keeps its scores and gains `judge_bundle: {folder, digest}`; it gets no `judge_status: pending`, so `judge_status_of` reads it as inline, gate G11 refuses nothing, and no `RESULT_COMPLETE` marker is written.
- Start rules for `inline+bundle`: one mode per sweep (mixing is refused as today); the code digest and the package digest are computed and required, like `deferred`; `evaluator_model` is required, `evaluator_model_revision` is not; `judge_bundle_dir` resolves like `deferred`.
- The bundle manifest records `judge_mode`. `publish.py` copies only bundles whose manifest mode is `deferred` (an absent key reads as `deferred`, for bundles written before this change), so an `inline+bundle` bundle never sits in the production queue as "not matched".
- `parse_judge_mode` accepts the new value and names all three allowed values when it refuses.
- `archi_judge.sbatch` gains the precision value `checkpoint`, for weights that ship pre-quantized (gpt-oss-120b MXFP4, Nemotron 3 Super FP8, Mistral Small 4 FP8): it emits `--dtype auto` and no `--quantization` flag, and the judge identity records `checkpoint` like the other precisions.
- Docs: the `judge_mode` row and a short `inline+bundle` paragraph in `docs/docs/benchmarking.md`; the `judge.env` precision comment names `checkpoint`.

No existing behaviour changes for `inline` (absent key) or `deferred` configs, apart from the one new manifest key.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `offline-judge-scoring`: "A deferred run writes a judge bundle instead of calling the judge" gains the `inline+bundle` mode (bundle first, then inline scoring; three allowed values); "A deferred result records a pending judge status" states that an `inline+bundle` arm carries `judge_bundle` and no `judge_status`; new requirements for the manifest's `judge_mode` and the publish filter, for the `inline+bundle` start rules, and for the `checkpoint` precision of the judge job.

## Impact

- `src/utils/judge_bundle.py`: `parse_judge_mode`, `plan_judge_run`, `write_judge_bundle` (manifest key), two mode predicates, one arm-fields helper.
- `src/bin/service_benchmark.py`: the three `is_deferred` call sites become thin calls to the new predicates and helper (write the bundle before the judge in `inline+bundle`; `RESULT_COMPLETE` stays deferred-only).
- `scripts/benchmarking/judge/publish.py`: skips bundles whose manifest mode is not `deferred`.
- `scripts/benchmarking/slurm/archi_judge.sbatch`: `checkpoint` precision.
- `docs/docs/benchmarking.md`, `openspec/specs/offline-judge-scoring/spec.md`.
- Tests: new files under `tests/unit/`; `test_judge_bundle_writer.py::test_manifest_has_exactly_the_d5_keys_and_values` and `test_judge_mode.py::test_invalid_raises_value_error_names_value_and_both_allowed` change to name the third value and the new key.
- Downstream (not in this change): switching the golden-set config in `fasrc/archi-config` to `judge_mode: inline+bundle` and redeploying; the judge-trial package in `fasrc/archi-bench-out` that scores these bundles on Slurm.
