## Why

A ragas-bench run (archi source `aa3f21e5`) crashed on question 7 of 9: the FASRCDocsAgent
built a 102,420-token prompt against QWEN's 32,768-token context window, vLLM returned an
`openai.BadRequestError` (HTTP 400, context-length-exceeded), the error was unhandled, and it
aborted the **entire** benchmark run — discarding the 6 already-answered questions and emitting
no scores. Two separate gaps caused one incident to become total loss: (a) the benchmark
harness has no per-question error isolation, and (b) the agent runtime has no guard for a
context-window overflow (unlike the recursion-limit overflow it already handles gracefully).
Gap (b) is a **production** crash — any real user asking a hard multi-part question can trigger
it, not just the benchmark.

## What Changes

- **Benchmark per-question error isolation.** The `service_benchmark.py` run loop wraps each
  question's answer + scoring so a single question's exception is caught, logged, and recorded
  as a clearly-marked failure row, then the run continues. One bad question can no longer
  discard the rest of the run's results. New resilience logic lives in a small unit-tested
  helper (the `src/bin` entrypoint stays a thin call site so patch coverage holds).
- **Agent context-overflow graceful degradation.** `base_react.py` `invoke()`/`stream()` gain
  a guard, mirroring the existing `GraphRecursionError` handler, that catches a genuine
  model-context-length-exceeded 400 and returns a graceful degraded `PipelineOutput` instead
  of crashing. Only true context-length overflow is degraded; other 400s (malformed params,
  etc.) are re-raised so real bugs still surface.
- **Out of scope (tracked separately as the accuracy work):** proactively *preventing* the
  overflow — history trimming/summarization, query-fusion-at-retrieval, retrieved-context
  token caps. This change is strictly reactive robustness, not accuracy.

## Capabilities

### New Capabilities
- `benchmark-run-resilience`: a benchmark run isolates per-question failures — one question's
  error is captured and recorded, never aborts the run or discards other questions' results.
- `agent-context-resilience`: the agent runtime degrades gracefully on a model-context-length
  overflow (returns a usable output), rather than propagating the provider 400 as a crash.

### Modified Capabilities
<!-- none: resilience is orthogonal to the existing retrieval-benchmarking A/B methodology spec -->

## Impact

- **Code:** `src/bin/service_benchmark.py` (run loop → thin call site) + a new tested helper
  module for #1; `src/archi/pipelines/agents/base_react.py` (`invoke`, `stream`, new
  `_handle_context_overflow_error`) for #2.
- **Behavior:** benchmark runs complete and emit scores even when some questions error;
  production chat degrades instead of crashing on context overflow.
- **No API/schema/dependency changes.** No config changes. Two independent Loop-2 PRs
  (harness; agent), each reviewable and mergeable on its own.
- **Not addressed here:** the root-cause context growth (large hierarchical parents × search
  budget spin × no trimming) — separate accuracy change.
