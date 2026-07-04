## 1. PR 1 — Agent context-overflow resilience (`base_react.py`)

- [ ] 1.1 RED: unit test that `_is_context_overflow_error()` returns True for the exact captured
  vLLM Q7 message ("You passed 102420 input tokens ... the model's context length is only 32768,
  resulting in a maximum input length of 32768 ...") — watch it fail against the current detector.
- [ ] 1.2 GREEN: broaden `_is_context_overflow_error()` with OpenAI-compatible phrasing
  (e.g. "context length is only", "maximum input length"), keeping existing patterns.
- [ ] 1.3 RED: unit test that `_is_context_overflow_error()` still returns True for the existing
  OpenAI-hosted phrasings (`context_length_exceeded`, "maximum context length") — no regression.
- [ ] 1.4 RED: unit test that `invoke()` returns a graceful degraded `PipelineOutput` (not raise)
  when the underlying `agent.invoke` raises a simulated context-overflow error — watch it fail
  (invoke currently has no overflow guard).
- [ ] 1.5 GREEN: add `except Exception as exc: if not self._is_context_overflow_error(exc): raise`
  → `return self._handle_context_overflow(error=exc, agent_inputs=agent_inputs, latest_messages=[])`
  to `invoke()`, after the `GraphRecursionError` branch, mirroring `stream()`/`astream()`.
- [ ] 1.6 RED: unit test that `invoke()` RE-RAISES a non-overflow 400 (e.g. malformed param) —
  watch it fail if the guard is too broad; confirm GREEN with D3's specific matching.
- [ ] 1.7 Refactor; run `conda run -n archi bash scripts/gate.sh` (patch coverage ≥ 80%); commit
  green.
- [ ] 1.8 Branch from `origin/dev`, push, open PR to `fasrc/archi:dev`; request `@codex review`;
  reply in-thread per finding; merge on green + `--delete-branch`; clean up local branch.

## 2. PR 2 — Benchmark per-question error isolation (`service_benchmark.py`)

- [ ] 2.1 RED: unit test for a new helper (e.g. `run_question_safely`) — a succeeding answer
  callable returns its result; a raising answer callable returns a marked failure entry with the
  captured error, without propagating. Watch it fail (helper does not exist yet).
- [ ] 2.2 GREEN: implement the helper in a unit-importable module; failure entry is distinct from
  a legitimate zero-score.
- [ ] 2.3 Wire `service_benchmark.py` `run()` loop (~line 1141) to call the helper as a thin call
  site: on failure, record the failure entry and `continue`; on success, proceed to scoring as
  today.
- [ ] 2.4 RED: unit test that aggregate scoring excludes failure entries from the success
  aggregate (a crashed question is not averaged in as a 0) and that an all-success run is
  unchanged vs prior behavior.
- [ ] 2.5 GREEN: adjust aggregation to honor the failure marking.
- [ ] 2.6 Refactor; run `conda run -n archi bash scripts/gate.sh` (patch coverage ≥ 80%); commit
  green.
- [ ] 2.7 Branch from `origin/dev`, push, open PR to `fasrc/archi:dev`; request `@codex review`;
  reply in-thread per finding; merge on green + `--delete-branch`; clean up local branch.

## 3. End-to-end verification

- [ ] 3.1 Re-run `archi evaluate -n ragas-bench -c config/benchmarking/ragas.yaml -e
  ~/.archi/.env.benchmark --hostmode --force`; confirm the run COMPLETES and emits scores, with
  any context-overflow question recorded as a graceful degraded answer / marked failure rather
  than crashing the run.
- [ ] 3.2 Capture the floor scores for the accuracy-work baseline (finding #3).

## 4. Archive

- [ ] 4.1 `/opsx:archive harden-benchmark-and-agent-resilience`; open the archive PR (no
  `@codex review`); merge.
