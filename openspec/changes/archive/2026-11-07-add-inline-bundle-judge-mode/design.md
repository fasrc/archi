## Context

`judge_mode` (archived change `2026-10-23-fix-issue-731-judge-deferred-bundle`, design D3–D9) has two values. `inline` scores each arm with the RAGAS judge during the run (`Benchmarker.get_ragas_results` → `score_ragas`); `deferred` writes the arm's judge bundle with `write_judge_bundle` and makes no judge call, and the arm record gets `pending_arm_fields` (`judge_status: pending`, `judge_bundle`). `plan_judge_run` checks every config once at start and returns a `JudgePlan(mode, arm_count, bundle_dir, code_version, package_digest)`; `is_deferred(plan)` gates the bundle write (`service_benchmark.py` ~2334), the arm fields (~877, through `handle_results(judge_bundle=...)`) and the `RESULT_COMPLETE` marker (~2718). `publish.py` copies every complete bundle under `<out_dir>/<judge_bundle_dir>/` to the queue and every `RESULT_COMPLETE` result whose arms are all pending.

The judge trial for #737 needs bundles of runs that were also scored by Sonnet. The only source today is a rebuild from the stack's Postgres volume (`/scratch/a2rchi/bench-runs/lynx-vs-ragas/rebuild_bundles.py`), which loses parent-less rows and depends on the volume surviving.

## Goals / Non-Goals

**Goals:**
- A run can keep its full judge input and still score inline, with one config key.
- The bundle exists even when the inline judge fails or is never reached.
- `deferred` runs, inline runs and the production queue behave as before.
- The judge job can serve pre-quantized weights without a wrong `--quantization` flag.

**Non-Goals:**
- Scoring `inline+bundle` bundles in the production queue (they are for trials and for Lynx; the merge path stays deferred-only).
- Writing the Sonnet scores into the bundle (they stay in the result JSON, joined by key).
- Any change to the result JSON of an `inline` run.
- The judge-trial scripts themselves (they live in `fasrc/archi-bench-out`).

## Decisions

**D1. A third mode value, `inline+bundle`, not a separate boolean.**
`parse_judge_mode` returns one of `"inline"`, `"deferred"`, `"inline+bundle"` (module constants `MODE_INLINE`, `MODE_DEFERRED`, `MODE_INLINE_BUNDLE`), and its refusal names all three. One enum keeps the "one mode per sweep" rule and the config template unchanged (`base-config.yaml` already renders `judge_mode` verbatim with `tojson`). *Alternative:* `write_judge_bundle: true` beside `judge_mode: inline` — two keys that can contradict each other (`true` with `deferred`) and a second mixed-sweep rule.

**D2. Two predicates replace `is_deferred` at the call sites.**
`writes_bundle(plan)` is true for `deferred` and `inline+bundle`; `scores_inline(plan)` is true for `inline` and `inline+bundle`; `is_deferred(plan)` stays for the deferred-only behaviours (`judge_status: pending`, `RESULT_COMPLETE`). `None` (a `Benchmarker` built without `__init__` in tests) reads as inline for all three. The call sites in `service_benchmark.py` stay one line each.

**D3. Start rules for `inline+bundle` follow `deferred`, minus the revision.**
`plan_judge_run` computes the code digest and the package digest and refuses when either is unavailable, because a bundle with no digests cannot be matched to a scorer later; it resolves `judge_bundle_dir` with the same relative-path rule; it requires `evaluator_model` (the inline judge must exist) and does **not** require `evaluator_model_revision`, because the inline judge is a hosted model (Bedrock Sonnet) with no revision. `_check_judge_model` takes a `require_revision` flag. *Alternative:* skip the digests for `inline+bundle` — cheaper, but then the trial scorer could not say which code produced the rows.

**D4. The bundle is written before the judge is built.**
In `_process_config`, when `writes_bundle(plan)`, the arm's bundle is written from `ragas_input` and the scorable keys exactly as the deferred branch does today; then, when `scores_inline(plan)`, the inline branch runs unchanged (`get_ragas_results`). An exception from the judge therefore never costs the bundle. `write_judge_bundle` is unchanged except for the manifest key of D5; the manifest's `judge_model.revision` is `None` when the config has none, which the writer already does.

**D5. The manifest records `judge_mode`, and `publish.py` filters on it.**
`write_judge_bundle` gains a `judge_mode` argument and manifest key. `publish.py::publish_bundle` returns without copying when `manifest.get("judge_mode", "deferred") != "deferred"`; the absent-key default keeps every bundle written before this change publishable. *Alternative:* have `publish.py` look up the result JSON's arm record — but the bundle exists before the result does, and the publisher runs on a timer, so it would publish the bundle on its first tick anyway.

**D6. Arm fields come from one helper.**
`judge_arm_fields(mode, judge_bundle)` returns `{}` for `inline`, `pending_arm_fields(judge_bundle)` for `deferred`, and `{"judge_bundle": {folder, digest}}` for `inline+bundle`. `handle_results` keeps its `judge_bundle` parameter (tests call it) and gains `judge_mode: str = MODE_DEFERRED`, so its existing callers are unchanged; `run()` passes `self.judge_plan.mode`. `judge_status_of` is untouched: an `inline+bundle` arm has no `judge_status` key and reads as `inline`, so G11 (`compare_runs.py`) and the campaign archiver accept it as a scored arm, and the merge never sees it.

**D7. `RESULT_COMPLETE` stays deferred-only.**
`publish.py` reads the marker as "this pending result is ready to queue"; an `inline+bundle` result is not pending. The call site keeps `is_deferred(plan)`.

**D8. `JUDGE_PRECISION=checkpoint` emits `--dtype auto` only.**
The `case` in `archi_judge.sbatch` gains `checkpoint) PRECISION_ARGS=(--dtype auto) ;;`. vLLM reads the quantization method from the checkpoint's `config.json` (`mxfp4`, `modelopt`, `fp8`); an explicit `--quantization fp8` on such weights asks for a second, dynamic quantization and is wrong. The scorer's identity records `JUDGE_PRECISION` as given, so `checkpoint` becomes part of the judge identity with no scorer change. The `judge.env` example keeps `bf16` (a docs test pins it) and its comment names the fourth value.

## Risks / Trade-offs

- [An `inline+bundle` bundle folder lands in `bench_out/feature_matrix/judge_bundles/` on the dev host, where no publisher consumes it] → It is the point: the trial package and Lynx read it in place; `banks/README.md` filing rules concern result JSONs, not this folder. The runbook says to copy bundles into the trial package.
- [A sweep author sets `inline+bundle` on one config and nothing on another] → Refused at start with both config names, by the existing mixed-mode rule.
- [The code or package digest is unavailable in a container that used to run `inline` fine] → Refused at start with the unavailable source named, like `deferred`; the operator can fall back to `inline`. The benchmark image always has `pip` and `src`, so this is a broken-environment signal, not a normal case.
- [A pre-quantized checkpoint served with `checkpoint` on hardware without the kernel for its method] → vLLM refuses at start, before any row is scored; the job log shows it. The trial runbook pairs each candidate with the precision its model card states.
- [`test_manifest_has_exactly_the_d5_keys_and_values` pins the manifest keys] → The test is updated to the D5 keys plus `judge_mode`; `load_bundle` and the Lynx reader check only the keys they use, so old bundles still load.

## Migration Plan

- Deploy: ordinary archi release; no data migration. A config that never sets `judge_mode` renders and runs as before.
- Turning it on for the golden set is a `fasrc/archi-config` change (`judge_mode: inline+bundle` in the golden-set benchmark config) followed by a redeploy; it is an operator step under #737, not part of this change.
- Rollback: set the config back to `inline` or remove the key; bundles already written stay valid.

## Open Questions

- None blocking. Whether the golden-set config should later move from `inline+bundle` to `deferred` (dropping Sonnet) is decided by the trial's result on #737.
