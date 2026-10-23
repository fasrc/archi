## Why

The RAGAS judge metrics are means of continuous scores, and the run-to-run deltas of these
means stay inside the repeat-run spread. The results that held up were binary per-row
outcomes with a paired exact test (the apology-row count, 6 to 0, p=0.006). PatronusAI Lynx
gives one PASS or FAIL faithfulness verdict for each row, so two arms can be compared row by
row with the exact McNemar test that `scripts/benchmarking/paired_tests.py` already has.
Lynx has open weights, so a weight revision pins the judge, and a cluster run costs nothing
per token.

Issue #734 (child 1 of 2 of #583, milestone `v2026.10.0`, P1) adds the scorer. The operator
decided on 2026-10-20 that Lynx reads the offload judge bundle that #731 writes (merged as
`c19f889e`, `src/utils/judge_bundle.py`). As a result, Lynx scores only `judge_mode:
deferred` runs.

The issue body dates from `7c7d7450`. The merged bundle code differs from it in four
places, and the issue says to obey the merged code:

- The bundle folder is `<out>/<judge_bundle_dir>/<basename>-arm<N>/`
  (`src/bin/service_benchmark.py:2290`, default `judge_bundles`), not `<out>/<basename>-arm<N>/`.
  The folder "beside the bundle" and the folder of the result JSON are different folders.
- The queue copy of a bundle has `READY`, not `BUNDLE_COMPLETE`
  (`scripts/benchmarking/judge/publish.py:40` copies only `rows.jsonl` and `manifest.json`).
- The bundle `key` is the positional `question_<n>` key (`src/bin/service_benchmark.py:2295`),
  but `compare_runs.py` pairs rows on question text (gate G5), because the positional key
  of one arm can name a different question in another arm.
- `arm_index` starts at 0 (`src/bin/service_benchmark.py:2496`), and the `@N` arm number
  of `compare_runs.py` starts at 1.

## What Changes

- New package `scripts/benchmarking/lynx/` (`prompt.py`, `parse.py`, `backends.py`,
  `score.py`, `report.py`), with only the standard library at module level.
- `prompt.py` fills the model-card prompt template, byte for byte, with QUESTION =
  `user_input`, DOCUMENT = `retrieved_contexts` joined with `"\n\n"`, and ANSWER =
  `response`.
- `parse.py` reads a strict PASS or FAIL verdict from the model output. Any other output
  is a parse error, never a verdict.
- `backends.py` has a stub backend for tests and a vLLM offline backend that imports
  `vllm` only in its constructor.
- `score.py` is the CLI: `python -m scripts.benchmarking.lynx.score <bundle folder>
  --model <id> --revision <sha> --out-dir <folder> [--backend vllm|stub]`. It refuses an
  incomplete or corrupt bundle, skips each row whose prompt does not fit in 8,000 tokens
  with 600 output tokens, and writes `<out-dir>/<basename>-arm<N>.lynx.json` outside the
  bundle folder. It never changes a file in the bundle folder.
- `compare_runs.py` finds the Lynx file of each arm beside the result JSON, shows a
  descriptive Lynx block (PASS rate and four counts), shows a refused block with the reason
  when the file does not match the arm, and adds a Lynx McNemar entry for each arm against
  the baseline over the questions that both arms scored.
- A result with no Lynx file loads and reports exactly as before.

## Capabilities

### New Capabilities

- `lynx-faithfulness-scoring`: the Lynx scorer over a judge bundle, its output file, and
  the Lynx block and paired test in `compare_runs.py`.

### Modified Capabilities

None. Lynx is not a RAGAS metric: it is not in `RAGAS_METRIC_NAMES`, in the `METRICS` of
`compare_runs.py`, in `ResultHandler.LEADERBOARD_METRICS`, or in any gate.

## Impact

- New: `scripts/benchmarking/lynx/__init__.py`, `prompt.py`, `parse.py`, `backends.py`,
  `score.py`, `report.py`, and new unit tests with a template fixture.
- Changed: `scripts/benchmarking/compare_runs.py` (call sites for the Lynx block, its
  rendering, and the Lynx paired entry).
- Not changed: the bundle writer, `publish.py`, the offload judge job, `src/utils/benchmark_schema.py`,
  `docs/`, and `fasrc/archi-config`.
- Not in scope: the Slurm job script and the docs (#735), and any release or comparison
  gate.
- Dependencies: none added. `vllm` and `transformers` are not in `pyproject.toml`; the
  scorer runs inside a vLLM container on the cluster (#735).
