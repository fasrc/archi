## Context

A ragas-bench run (archi `aa3f21e5`) crashed on question 7/9: the FASRCDocsAgent assembled a
102,420-token prompt against QWEN's 32,768-token window, vLLM returned
`openai.BadRequestError` (HTTP 400, message *"the model's context length is only 32768,
resulting in a maximum input length of 32768"*), and the unhandled error aborted the whole run,
discarding six answered questions.

Reading the code changes the shape of the fix from the naive framing:

- `base_react.py` **already has** a context-overflow path: `_is_context_overflow_error()`
  (line ~1768) and `_handle_context_overflow()` (line ~1779, which even retries with the last
  user message only). `stream()` (line ~546) and `astream()` (line ~856) already call it.
  Only `invoke()` (line ~305) is missing the guard — it catches `GraphRecursionError` and
  nothing else. The benchmark drives `invoke()`, so it crashed.
- **But the detector is also too narrow.** `_is_context_overflow_error()` matches only
  `"ContextOverflow"`, `"context_length_exceeded"`, `"Input tokens exceed"`, and
  `"maximum context length"`. vLLM's actual message contains *"context length is only …
  maximum input length"* — none of those substrings. So even the already-guarded `stream()`/
  `astream()` would have re-raised this error. The detector fix is what makes the guard fire
  for the error we actually hit.
- `service_benchmark.py` `run()` (line ~1141) calls `self.chain(...)` with no per-question
  `try/except`; it already `continue`s on per-question validation errors (lines ~1119-1128),
  so the pattern to extend is established.

## Goals / Non-Goals

**Goals:**
- One question's failure never aborts a benchmark run or discards other questions' results.
- The agent degrades gracefully (not crash) when the prompt exceeds the model context window,
  on the `invoke()` path and for OpenAI-compatible (vLLM) error phrasing.
- Reuse existing machinery; no new dependencies, no config, no schema change.

**Non-Goals:**
- Preventing the overflow (history trimming/summarization, query-fusion-at-retrieval,
  retrieved-context token caps). That is the separate accuracy change (finding #3).
- Changing scoring math, RAGAS metrics, or the benchmark output schema beyond adding a
  clearly-marked failure entry.

## Decisions

### D1 — #1: isolate per-question failures via a tested helper
Wrap the answer **and per-question scoring** body of the `run()` loop so any exception is caught,
logged, and recorded as a failure entry (question text + captured error), then `continue`. The
boundary MUST span the per-question scoring that runs inside the loop after `self.chain(...)` —
`get_source_results` (source matching) and the RAGAS-input assembly / `question_wise_results`
population — not just the answer call, since those can raise too (Codex F1). The aggregate RAGAS
computation (`Dataset.from_list` after the loop) is a separate concern handled by D4. Because
`src/bin/service_benchmark.py` is a service entrypoint that unit tests do not import (same
constraint as `interfaces/chat_app/app.py`), the resilience logic goes in a small
unit-importable helper (e.g. `run_question_safely(answer_and_score_callable) -> (result | failure)`),
and the loop becomes a thin call site. Tests exercise the helper directly with a succeeding and
a raising callable.
- *Alternative — inline try/except in the loop:* rejected; the new lines would not be covered
  by unit tests and would fail diff-cover.
- *Alternative — wrap only the answer call:* rejected (Codex F1); per-question scoring runs in
  the same loop iteration and would still abort the run if it raised.
- *Alternative — mark failed questions as zero-score:* rejected as the default; a failure is
  recorded distinctly, not silently averaged in as a legitimate 0 (a crashed question and a
  wrong-but-answered question are different signals).

### D4 — #1: all-failed configuration emits skipped aggregates, never empty RAGAS (Codex F2)
When a configuration ends with zero successful questions, `ragas_input` is empty; the current
path (`Dataset.from_list(ragas_input)` → `get_ragas_results`) would build an empty dataset and
then access metric columns that do not exist. Guard the aggregation: if the success set is
empty, skip RAGAS for that configuration and record marked `n/a` / skipped aggregates so the run
still completes. Unit-test the all-failed case explicitly.
- *Alternative — let RAGAS handle empty input:* rejected; empty aggregation raises or yields
  meaningless columns, re-introducing the "run doesn't complete" failure this change removes.

### D5 — #1: failure rows excluded from human-eval consumers (Codex F4)
Failure entries live in `question_wise_results` / `single_question_results`, which are iterated
by `pair_ab_results` (A/B comparison) and `push_single_results_to_argilla` (human-eval export).
Left unfiltered, a failed question enters A/B pairing and Argilla as a blank-answer gradeable
record and skews evaluation. Mark failure entries and have both consumers skip (or explicitly
flag) them. Unit-test that a failure entry is not emitted as a normal record by either consumer.
- *Alternative — leave consumers as-is:* rejected; silent blank-answer records corrupt human
  evaluation and A/B deltas.

### D6 — #2: a recovered overflow retry stays marked degraded (Codex F3)
The shared `_handle_context_overflow` retry-success path returns the recovered answer with only
a `context_overflow_retry` metadata flag and no user-facing message. Rather than inject a
user-facing message into the shared helper (which would also change production chat UX on a
successful recovery), the marker `context_overflow_retry` is treated as the degraded signal: the
benchmark records such rows as degraded, and the agent-context-resilience spec requires the
output be *distinguishable* from a clean success (message on hard failure, metadata marker on
recovered retry). Add a retry-success unit test asserting the marker is present.
- *Alternative — force a visible "trimmed context" message into the shared helper:* rejected;
  it changes production chat behavior for every successful long-conversation recovery, beyond
  the scope of this reactive fix. The metadata marker satisfies "message **or marker**."

### D2 — #2a: add the existing overflow guard to `invoke()`
After the `except GraphRecursionError` branch in `invoke()`, add
`except Exception as exc: if not self._is_context_overflow_error(exc): raise` →
`return self._handle_context_overflow(error=exc, agent_inputs=agent_inputs, latest_messages=[])`,
mirroring `stream()`/`astream()` exactly. Non-overflow errors re-raise unchanged so real bugs
still surface.
- *Alternative — catch only in the benchmark (D1) and skip the agent:* rejected; the crash is
  a production runtime bug reachable by real users, not just the benchmark. It must degrade at
  the agent boundary.

### D3 — #2b: broaden `_is_context_overflow_error()` to OpenAI-compatible phrasing
Add substring checks that match vLLM's message form — e.g. `"context length is only"`,
`"maximum input length"` — alongside the existing patterns, keeping the existing ones for no
regression. TDD anchored on the **exact** captured Q7 message string. This single fix protects
`invoke()`, `stream()`, and `astream()` at once, since all three route through the detector.
- *Alternative — match the HTTP 400 status code instead of message text:* rejected; a bare 400
  is also emitted for malformed params, and we must NOT degrade those (they should surface).
  Message-substring matching keeps degradation scoped to genuine context overflow.

## Risks / Trade-offs

- **Over-broad detector swallows unrelated errors** → mitigate with specific, phrase-level
  substrings (not a bare "context length" or a status code) and a default `raise`; unit tests
  assert a malformed-param 400 is re-raised.
- **The `_handle_context_overflow` retry itself overflows again** → already bounded: it retries
  with the last human message only at `recursion_limit=10` and falls back to a plain error
  message if that also fails. No change needed.
- **Benchmark failure entry pollutes aggregates** → failure entries are marked and excluded
  from the success aggregate, not counted as legitimate zero-scores; tests assert the
  distinction.
- **Low residual risk overall:** both fixes reuse established patterns/helpers, touch no
  config/schema/deps, and are independently revertable (two PRs).

## Migration Plan

No migration. Pure additive robustness. Deploy via the normal path; rollback is reverting
either PR independently. The fix is verifiable by re-running ragas-bench (the crash becomes a
recorded per-question failure with scores emitted for the rest) and by a unit test feeding the
captured Q7 error string through the agent boundary.

## Open Questions

- Should a benchmark whose failure rate exceeds some threshold exit non-zero (CI signal), or
  always exit zero with failures recorded? Default assumption: record failures, still emit
  scores, exit zero; revisit if CI needs a hard gate.
