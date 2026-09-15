## 1. PR 1 — Agent context-overflow resilience (`base_react.py`)

- [x] 1.1 RED: unit test that `_is_context_overflow_error()` returns True for the exact captured
  vLLM Q7 message ("You passed 102420 input tokens ... the model's context length is only 32768,
  resulting in a maximum input length of 32768 ...") — watch it fail against the current detector.
- [x] 1.2 GREEN: broaden `_is_context_overflow_error()` with OpenAI-compatible phrasing
  (e.g. "context length is only", "maximum input length"), keeping existing patterns.
- [x] 1.3 RED: unit test that `_is_context_overflow_error()` still returns True for the existing
  OpenAI-hosted phrasings (`context_length_exceeded`, "maximum context length") — no regression.
- [x] 1.4 RED: unit test that `invoke()` returns a graceful degraded `PipelineOutput` (not raise)
  when the underlying `agent.invoke` raises a simulated context-overflow error — watch it fail
  (invoke currently has no overflow guard).
- [x] 1.5 GREEN: add `except Exception as exc: if not self._is_context_overflow_error(exc): raise`
  → `return self._handle_context_overflow(error=exc, agent_inputs=agent_inputs, latest_messages=[])`
  to `invoke()`, after the `GraphRecursionError` branch, mirroring `stream()`/`astream()`.
- [x] 1.6 RED: unit test that `invoke()` RE-RAISES a non-overflow 400 (e.g. malformed param) —
  watch it fail if the guard is too broad; confirm GREEN with D3's specific matching.
- [x] 1.7 RED: unit test that when the trimmed-context retry SUCCEEDS, `invoke()` returns the
  recovered answer marked with the `context_overflow_retry` metadata (distinguishable from a
  clean success) — per D6 / Codex F3. GREEN: the existing marker already carries this; assert it
  flows through `invoke()`.
- [x] 1.8 Refactor; run `conda run -n archi bash scripts/gate.sh` (patch coverage ≥ 80%); commit
  green.
- [x] 1.9 Branch from `origin/dev`, push, open PR to `fasrc/archi:dev`; request `@codex review`;
  reply in-thread per finding; merge on green + `--delete-branch`; clean up local branch.

## 2. PR 2 — Benchmark per-question error isolation (`service_benchmark.py`)

- [x] 2.1 RED: unit test for a new helper (e.g. `run_question_safely`) — a succeeding
  answer+score callable returns its result; a raising callable returns a marked failure entry
  with the captured error, without propagating. Watch it fail (helper does not exist yet).
- [x] 2.2 GREEN: implement the helper in a unit-importable module; failure entry is distinct from
  a legitimate zero-score.
- [x] 2.3 Wire `service_benchmark.py` `run()` loop (~line 1141) to call the helper as a thin call
  site, wrapping the answer AND the per-question scoring that follows it in the same iteration
  (`get_source_results`, RAGAS-input assembly, `question_wise_results` population — Codex F1): on
  failure, record the failure entry and `continue`; on success, proceed as today.
- [x] 2.4 RED: unit test that aggregate scoring excludes failure entries from the success
  aggregate (a crashed question is not averaged in as a 0) and that an all-success run is
  unchanged vs prior behavior.
- [x] 2.5 GREEN: adjust aggregation to honor the failure marking.
- [x] 2.6 RED: unit test the ALL-FAILED configuration (Codex F2) — every question fails →
  `ragas_input` empty. Assert the run completes, RAGAS is skipped (no `Dataset.from_list` on
  empty input), and the aggregate is recorded as `n/a`/skipped. GREEN: guard the aggregation on
  an empty success set (D4).
- [x] 2.7 RED: unit test that failure entries are excluded/flagged in the human-eval consumers
  (Codex F4) — `pair_ab_results` does not pair a failed question as a real answer, and
  `push_single_results_to_argilla` does not emit it as a normal blank-answer record. GREEN: mark
  failure entries and have both consumers skip/flag them (D5).
- [x] 2.8 Refactor; run `conda run -n archi bash scripts/gate.sh` (patch coverage ≥ 80%); commit
  green.
- [x] 2.9 Branch from `origin/dev`, push, open PR to `fasrc/archi:dev`; request `@codex review`;
  reply in-thread per finding; merge on green + `--delete-branch`; clean up local branch.

## 3. End-to-end verification

- [x] 3.1 Re-run `archi evaluate -n ragas-bench -c config/benchmarking/ragas.yaml -e
  ~/.archi/.env.benchmark --hostmode --force`; confirm the run COMPLETES and emits scores, with
  any context-overflow question recorded as a graceful degraded answer / marked failure rather
  than crashing the run.
- [x] 3.2 Capture the floor scores for the accuracy-work baseline (finding #3).

## 4. Archive

- [x] 4.1 `/opsx:archive harden-benchmark-and-agent-resilience`; open the archive PR (no
  `@codex review`); merge.

## 5. Delivery notes

- **PR 1 (#91)** — agent context-overflow resilience in `base_react.py`: broadened
  `_is_context_overflow_error()` for vLLM/OpenAI-compatible phrasing; added the `invoke()`
  overflow guard mirroring `stream()`/`astream()`; fixed `_handle_context_overflow` to trim to the
  last *human* message (Codex F1).
- **PR 2 (#92)** — benchmark per-question isolation in `service_benchmark.py` +
  `benchmark_resilience.py`: `_answer_and_score_question` isolates per-question failures;
  degraded (context-overflow) answers marked and excluded from RAGAS + source scoring; all-failed
  config yields NaN aggregates (no empty `Dataset.from_list`); `pair_ab_results` and the Argilla
  export skip non-scorable rows. Codex P1 (scorable subset alignment) + 4 more fixed.
- **PR 3 (#94)** — emergent follow-up surfaced by the floor re-run: the HTML report generator
  (`generate_benchmark_report.py`) read `reference_sources_metadata[i]["matched"]` unconditionally,
  but degraded/failed rows never stamp `matched`; a degraded row crashed report rendering with
  `KeyError('matched')` after scores were dumped. Fixed with `.get("matched")`. This was the last
  unguarded consumer of the degraded-row marking (completes the D5 "consumers tolerate degraded
  rows" requirement of the `benchmark-run-resilience` capability).
- **3.1 / 3.2 (floor re-run, 2026-07-04)** — `archi evaluate -n ragas-bench --hostmode --force`
  ran to completion (previously aborted on the overflow question). `question_6` ("interactive Slurm
  session with one GPU for 2h on Cannon") was recorded as `degraded` and excluded, and the run
  produced scores. Floor baseline (8 scorable): answer_relevancy **0.862**, faithfulness **0.594**,
  context_precision **0.501**, context_recall **0.667**, relative_source_accuracy **0.556**,
  source_accuracy **0.000** (corpus snapshot `5fdd94d1`; judge HUIT Bedrock Sonnet 4.5).
