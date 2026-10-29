Rules for every task:
- Read `design.md` (D1–D8) before task 1.1. Where this change and the archived
  `2026-10-23-fix-issue-731-judge-deferred-bundle` differ, this change wins.
- Each checkbox is ONE commit. Inside each task: write the failing test first, run it and
  watch it FAIL for the right reason, then write the minimum code, then watch it pass.
  Never commit a red test.
- Run `bash scripts/gate.sh` before each commit (black 24.10.0 + isort 6.0.1, `pytest
  tests/unit/`, diff-cover patch coverage ≥ 80% vs `origin/dev`). Commit only when it is
  green. Never use `--no-verify`. No `Co-Authored-By` or session trailers. Short,
  lowercase commit messages.
- Keep new logic in `src/utils/judge_bundle.py`. The lines in
  `src/bin/service_benchmark.py` are thin call sites (run `black-seam-scout` on it first).
- Put new tests in NEW files with unique names. The only edits to existing test files are
  `tests/unit/test_judge_mode.py` (the parametrized bad-values test asserts the third
  value in the message) and `tests/unit/test_judge_bundle_writer.py`
  (`test_manifest_has_exactly_the_d5_keys_and_values` adds `judge_mode`).
- No test needs a GPU, Slurm, network access, or a real `pip freeze` run. Patch the
  `ResultHandler` class attributes and `OUTPUT_DIR` with `monkeypatch` as
  `tests/unit/test_judge_offload_e2e.py` does.
- Do not change the inline path's result JSON: an `inline` arm gets no new key.

## 1. Mode value and predicates (D1, D2)

- [x] 1.1 `parse_judge_mode` accepts `inline+bundle`; constants `MODE_INLINE`, `MODE_DEFERRED`, `MODE_INLINE_BUNDLE`; the refusal names all three values. Add `writes_bundle(plan)` and `scores_inline(plan)` beside `is_deferred(plan)`, `None` reading as inline. Tests: new `tests/unit/test_judge_mode_inline_bundle.py`; update the message assertion in `test_judge_mode.py`.

## 2. Start rules (D3)

- [x] 2.1 `plan_judge_run` treats `inline+bundle` like `deferred` for digests and `judge_bundle_dir`, requires `evaluator_model` and not `evaluator_model_revision` (`_check_judge_model(..., require_revision=...)`); mixed `inline+bundle`/absent is refused naming both configs. Tests: new `tests/unit/test_judge_plan_inline_bundle.py` (plan carries digests and bundle_dir; no-revision starts; unavailable digest refuses and quotes the source; mixed refuses).

## 3. Manifest key and arm fields (D5, D6)

- [x] 3.1 `write_judge_bundle(..., judge_mode=...)` records `judge_mode` in the manifest; `judge_arm_fields(mode, judge_bundle)` returns `{}` / pending fields / `{"judge_bundle": …}`. Tests: new `tests/unit/test_judge_bundle_inline_fields.py`; add `judge_mode` to the exact-keys test in `test_judge_bundle_writer.py`. Update the existing `write_judge_bundle` callers (service_benchmark deferred branch, lynx-vs-ragas is outside the repo).

## 4. Benchmark call sites (D4, D6, D7)

- [x] 4.1 `_process_config`: when `writes_bundle(plan)`, write the bundle (passing the plan's mode) before any judge or embedding object is built; when `scores_inline(plan)`, run the inline branch unchanged. `handle_results` gains `judge_mode: str = MODE_DEFERRED` and applies `judge_arm_fields`; `run()` passes `self.judge_plan.mode`. `RESULT_COMPLETE` stays behind `is_deferred`. Tests: new `tests/unit/test_judge_inline_bundle_e2e.py` modelled on `test_judge_offload_e2e.py`: (a) 3 scorable rows → bundle with 3 full-context rows and manifest `judge_mode: inline+bundle`, judge factory called, aggregates present, arm has `judge_bundle` and no `judge_status`, no `RESULT_COMPLETE`; (b) a judge factory that raises → bundle folder with `BUNDLE_COMPLETE` exists and the run handles the failure as inline does; (c) an inline config still writes no bundle and no new key.

## 5. Publish filter (D5)

- [x] 5.1 `publish.py::publish_bundle` returns before copying when `manifest.get("judge_mode", "deferred") != "deferred"`, with no conflict. Tests: new `tests/unit/test_judge_publish_mode_filter.py` (inline+bundle bundle not copied, exit 0; manifest without the key copied as before).

## 6. Judge job precision (D8)

- [x] 6.1 `archi_judge.sbatch`: `checkpoint) PRECISION_ARGS=(--dtype auto) ;;`, and the comment block names it. Tests: new `tests/unit/test_judge_slurm_checkpoint_precision.py` using the stub harness of `test_judge_slurm.py` (dry run shows `--dtype auto` and no `--quantization`; `int3` still refused).

## 7. Docs and spec

- [x] 7.1 `docs/docs/benchmarking.md`: the `judge_mode` table row names the three values; a short paragraph after the table describes `inline+bundle` (bundle first, inline scores, `judge_bundle` in the arm, no queue publish); the `judge.env` example comment reads `# bf16, fp16, fp8, or checkpoint (pre-quantized weights: no quantization flag)` with the value left at `bf16`. Test: new `tests/unit/test_benchmarking_docs_inline_bundle.py` asserting the row and the paragraph. Keep `test_benchmarking_docs_judge.py` green.

## 8. Gate and finish

- [ ] 8.1 `bash scripts/gate.sh` green on the branch; `openspec validate add-inline-bundle-judge-mode --strict` passes; commit. Ask the operator before any push or PR.
