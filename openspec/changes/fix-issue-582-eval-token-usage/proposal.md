## Why

Nobody can price an evaluation run from its artifacts today. archi reaches Bedrock through
HUIT's gateway, the AWS billing APIs belong to HUIT's account, and the only cost signal we
own is the per-response `usage` block. `HuitBedrockChatModel` already parses that block into
`AIMessage.usage_metadata` (`src/archi/providers/huit_bedrock_provider.py:296-306`), and then
every evaluation path drops it:

- `grep -rn usage_metadata src/evaluation/qa/ src/bin/service_benchmark.py` matches nothing.
- QA `preparation.jsonl` (gold-atom extraction), `answers.jsonl` (tested agent) and
  `evaluation_results.jsonl` (comparator judgments) carry no token counts.
- The RAGAS benchmark artifact's per-arm `benchmarking_results[*]` entries record
  `ragas_effective_settings` but no judge usage.

The rung-0 atoms campaign (fasrc/archi#540 follow-up) makes about 109 extractor calls,
6 × 109 QA judgments and 2 × 3 × 109 × 4 RAGAS metric calls. None of them is priceable.
Issue: fasrc/archi#582.

## What Changes

- New tested helper `src/utils/llm_usage.py`: a langchain callback handler
  (`UsageRecorder`) that sums `AIMessage.usage_metadata` per `(provider, model)`, counts
  calls that reported no usage, and emits one JSON-safe `usage` object; plus `sum_usage` to
  add `usage` objects into a phase total. Tokens only — no prices, no dollar amounts.
- QA workflow (additive keys only):
  - `preparation.jsonl`: a `usage` key on rows whose gold atoms the extractor inferred
    (`prepared` with `atom_source: "inferred"`, and `preparation_failed` when the extractor
    ran). The row loader accepts the key as optional, so old files still load.
  - `answers.jsonl`: a `usage` key next to `duration_ms` on `answer_ready` and
    `execution_failed` rows (tested-agent tokens, where the provider reports them).
  - `evaluation_results.jsonl`: a `usage` key on `scored` and `evaluation_failed` rows
    (comparator tokens).
  - `summary.json`: `provenance.usage = {"prepare": …, "run": …, "score": …}`, each the sum
    of that phase's row `usage` objects, or `null` when no row in the phase carries one.
- RAGAS benchmark: a `judge_usage` key on each `benchmarking_results[*]` entry, the sum of
  every judge call ragas made for that arm; `null` when no judge ran.
- Docs: one section in `docs/docs/evaluation.md` — how to price a run from the recorded
  tokens (tokens × the reader's own per-token rate; arithmetic only, no rates in the repo).

## Non-goals

- No dollar amounts, rate tables or pricing code anywhere.
- No `compare_runs.py` token block (issue plan step 4, "optional follow-up"). `compare_runs`
  is not changed; a test proves it still loads a run directory that carries the new keys.
- No SUT (system-under-test) usage in the benchmark artifact. The benchmark chat path does
  not expose per-question messages through one seam; that is a separate change.
- No change to `huit_bedrock_provider.py` or any other provider.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `qa-evaluation-trial`: QA run artifacts record per-call LLM token usage and phase totals.
- `retrieval-benchmarking`: each benchmark arm records the RAGAS judge's token usage.

## Impact

- Code: new `src/utils/llm_usage.py`; edits in `src/evaluation/qa/runtime.py`,
  `src/evaluation/qa/preparation.py`, `src/evaluation/qa/phases.py`,
  `src/evaluation/qa/workflow.py`, `src/bin/service_benchmark.py`.
- Tests: new `tests/unit/test_llm_usage.py`; new tests in existing QA and benchmark test
  modules.
- Artifacts: additive keys only. Readers that ignore unknown keys are unaffected; the one
  strict reader (`_record_from_row` in `preparation.py`) is widened to accept `usage`.
- Acceptance that needs the live HUIT key (the issue's smoke command) is a post-merge
  operator check, not a gate item.
