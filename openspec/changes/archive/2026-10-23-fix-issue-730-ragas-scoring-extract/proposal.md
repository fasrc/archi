## Why

The offline judge job (#732) must score rows with the same RAGAS code as the inline
benchmark path. Today that code lives inside `Benchmarker` in
`src/bin/service_benchmark.py`, so no other caller can use it. Issue #730 (PR 1 of the
change `offload-ragas-judge-to-slurm`, parent #729, milestone `v2026.10.0`, P1) moves the
scoring into a new module, `src/utils/ragas_scoring.py`, with no change in behavior. It
also adds the judge-client seam that the offline job needs: a client that reaches the
judge only through a Unix socket, with a key from the environment.

**This change is a driver for the nightly executor only.** The design of record is
`openspec/changes/offload-ragas-judge-to-slurm/` (design D2 and D6, tasks 1.1–1.4). Issue
#730 says "Do not create a new OpenSpec change". The nightly supervisor reads its task list
from `openspec/changes/fix-issue-<n>-<slug>/tasks.md`, so this folder exists to hold that
list. It adds no design of its own. Where it is more exact than the parent change, the
reason is a fact that the issue body or the tree states (see `design.md`), and the PR
corrects the parent change to agree.

## What Changes

- New module `src/utils/ragas_scoring.py`. It holds the RAGAS evaluate wiring
  (`RunConfig` assembly, `batch_size`, the `UsageRecorder` callback, the per-metric
  `evaluate` call), the judge-LLM builder (the provider `match` of
  `get_ragas_llm_evaluator`), and the embedding builder (the `match` of
  `get_ragas_embedding_model`).
- `Benchmarker.get_ragas_results`, `get_ragas_llm_evaluator` and
  `get_ragas_embedding_model` become thin wrappers over the module. They keep the three
  side effects that landed after the design: `self._judge_usage` (#585),
  `self._ragas_scored` (#518) and `self._ragas_judge_identity` (#618).
- The pure helpers stay where they are and are imported, not copied:
  `score_metrics_per_eligibility`, `build_ragas_metric_objects`, `ragas_result_column`,
  `ragas_run_config_kwargs`, `ragas_effective_settings` (`src/utils/benchmark_schema.py`).
- New builder in the module: a judge LLM whose HTTP transport connects to a Unix socket,
  with a bearer key read from an environment variable. The inline path does not call it
  yet. The offline scorer (#732) will.
- A characterization test pins today's results through `Benchmarker.get_ragas_results`.
  It passes before and after the move, with no edit.
- The PR ticks tasks 1.1–1.4 in `offload-ragas-judge-to-slurm/tasks.md`, and corrects
  design D2 there to the narrower scope.
- **No** change to result JSON keys, `handle_results`, `ResultHandler`, `run()` or
  `_process_config`. **No** `judge_mode`, bundle, publish, scorer, merge or Slurm work
  (groups 2–5 of the parent change; #731–#733).

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `retrieval-benchmarking`: adds two requirements. The benchmark RAGAS scoring keeps its
  results when it moves to a shared module, and the shared judge builder can reach a
  judge through a Unix socket with a key from the environment.

## Impact

- `src/utils/ragas_scoring.py` (new), `src/bin/service_benchmark.py` (thin wrappers and
  one import).
- `tests/unit/test_ragas_scoring_characterization.py` (new; never edited after its first
  commit), `tests/unit/test_ragas_scoring.py` (new).
- `openspec/changes/offload-ragas-judge-to-slurm/tasks.md` and `design.md` (tick 1.1–1.4,
  correct D2).
- No new dependency: the base images pin `httpx==0.27.2`
  (`src/cli/templates/dockerfiles/base-python-image/requirements.txt:27`),
  `src/evaluation/qa/oracle_config.py:15` already imports it, and
  `httpx.HTTPTransport` accepts `uds=`.
