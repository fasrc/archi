## Why

Golden-set RAGAS runs send every judge call to the Bedrock Sonnet judge, which bills the
team and throttles (parent #729). The change `offload-ragas-judge-to-slurm` moves the judge
to a model with open weights on the cluster. PR 1 (#730, merged as #738) moved the scoring
into `src/utils/ragas_scoring.py`. Issue #731 (PR 2, milestone `v2026.10.0`, P1) adds the
first half of the offload: a run that answers every question, makes no judge call, and
writes a **judge bundle** for each arm, plus a publish script that moves complete bundles
to a queue folder. Nothing scores a bundle yet (#732).

**This change is a driver for the nightly executor only.** The design of record is
`openspec/changes/offload-ragas-judge-to-slurm/` (design D3, D3a, D8a and D10; tasks
2.1–2.6 with 2.2a, 2.2b and 2.5a). Issue #731 says "Do not create a new OpenSpec change".
The nightly supervisor reads its task list from
`openspec/changes/fix-issue-<n>-<slug>/tasks.md`, so this folder exists to hold that list.
It adds no second design. Where it is more exact than the parent change, the reason is a
fact that the issue body or the tree states, or a gap that the parent leaves open (see
`design.md`). The PR corrects the parent change to agree.

## What Changes

- New optional `ragas_settings` keys: `judge_mode` (`inline` or `deferred`; absent means
  `inline`), `judge_bundle_dir` (a folder under the benchmark output folder; default
  `judge_bundles`) and `evaluator_model_revision` (the judge revision that a deferred
  bundle asks for). `base-config.yaml` renders each key only when it is set, so an inline
  config renders exactly as before.
- A run fixes its result basename (`<benchmark_name>-<UTC timestamp>`) once, before the
  first arm. The result JSON, the `_report.md` and the category-map TSVs use it.
- Before the first arm, the run refuses to start when its configs mix judge modes (it
  names the configs), and a deferred run refuses to start when its code digest is
  unavailable, its package digest is unavailable, its judge model id or revision is
  empty, or its configs name different bundle folders.
- In a deferred arm, `_process_config` writes a bundle
  (`<out>/<judge_bundle_dir>/<basename>-arm<N>/`: `rows.jsonl`, `manifest.json`, then
  `BUNDLE_COMPLETE`) in place of the judge call. The arm records `judge_status: pending`
  and the bundle digest, keeps the `null` judge fields of #518, and has no RAGAS
  aggregates. After `dump_artifacts` returns, a deferred run writes
  `<basename>.RESULT_COMPLETE`.
- New `src/utils/judge_bundle.py` (mode parsing, the start check, the bundle writer, the
  status reader), new `src/utils/exclusive_link.py` (the no-replace `link` primitive of
  design D3), and a package-digest helper in `src/utils/benchmark_provenance.py`.
- New `scripts/benchmarking/judge/publish.py`: copies complete bundles to a queue folder
  that is a required argument, checks digests, writes `READY` last, copies a complete
  pending result JSON to `<queue>/results/`, and copies a merged judged file back.
  `run_goldenset_eval.sh` does not call it.
- The PR ticks tasks 2.1–2.6 (with 2.2a, 2.2b, 2.5a) in
  `offload-ragas-judge-to-slurm/tasks.md`, and corrects that change's design and spec to
  agree with this one.
- **No** scorer, claim, checkpoint, judge identity or merge (#732). **No** Slurm scripts,
  gates or docs (#733). **No** task 2.7 (systemd units in `fasrc/archi-config`, an
  operator step on #729).

## Capabilities

### New Capabilities
<!-- None. `offline-judge-scoring` is the parent change's new capability; this driver does
not add to it, to avoid an archive collision. -->

### Modified Capabilities
- `retrieval-benchmarking`: adds requirements for the judge mode, the fixed result
  basename, the deferred bundle and its pending status, the exclusive-link primitive, and
  the publish step.

## Impact

- `src/utils/judge_bundle.py` (new), `src/utils/exclusive_link.py` (new),
  `src/utils/benchmark_provenance.py` (package digest), `src/bin/service_benchmark.py`
  (thin call sites in `Benchmarker.__init__`, `run()`, `_process_config`,
  `ResultHandler.handle_results` and `ResultHandler.dump_artifacts`),
  `src/cli/templates/base-config.yaml` (three conditional keys).
- `scripts/benchmarking/judge/publish.py` (new).
- New tests under `tests/unit/`. Two stub lambdas in
  `tests/unit/test_benchmark_resilience.py` accept the new keyword of `dump_artifacts`.
- `openspec/changes/offload-ragas-judge-to-slurm/` `tasks.md`, `design.md` and
  `specs/offline-judge-scoring/spec.md` (ticks and corrections).
- No new dependency. Config is additive; old result JSONs load as `inline`.
