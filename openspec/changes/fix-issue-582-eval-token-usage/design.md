## Context

Measured at `origin/dev` `e58a7ada` (the branch base).

- `HuitBedrockChatModel._generate` puts `{"input_tokens", "output_tokens", "total_tokens"}`
  into `AIMessage.usage_metadata` and sets **no** `response_metadata["model_name"]`
  (`src/archi/providers/huit_bedrock_provider.py:296-308`).
- QA evaluator calls: `LangChainEvaluatorRuntime._structured` does
  `model.with_structured_output(schema).invoke([...])` and returns only the parsed dict
  (`src/evaluation/qa/runtime.py:219-234`). The component models come from
  `profile.components()` → `descriptor.provider`, `descriptor.model`
  (`runtime.py:200-217`). `extract_gold` uses component `atoms_extractor`; `compare` uses
  component `evaluator`.
- QA tested agent: `ArchiAgentRuntime.run` already passes `callbacks=[timing_callback]` to
  `pipeline.invoke` and exposes the result as `self.tool_calls`
  (`runtime.py:402-420`). Provider and model are
  `config["services"]["chat_app"]["default_provider" | "default_model"]` (`runtime.py:374`).
- Row writers: `prepare_dataset_item` → `PreparationRecord.to_dict()`
  (`preparation.py:268-367`, `219-260`); `run_attempt` (`phases.py:42-71`); `score_answer`
  (`phases.py:108-139`). Summary provenance is built twice in `workflow.py` — the score path
  (≈`:862`) and the retry path (≈`:1209`).
- The only strict reader of these rows is `_record_from_row` (`preparation.py:384-480`,
  exact keys via `_require_exact_keys`). `history.py`'s answer-row reader checks
  `duration_ms` and `tool_calls` only; `compare_runs.load_qa_run`
  (`scripts/benchmarking/compare_runs.py:1911`) reads rows as dicts.
- RAGAS: `Benchmarker.get_ragas_results` builds `LangchainLLMWrapper(self.get_ragas_llm_evaluator())`
  and calls ragas `evaluate(...)` once per enabled metric inside `score_fn`
  (`service_benchmark.py:1927-2006`). ragas `evaluate` accepts `callbacks` (langchain
  callbacks, run for every LLM call it makes). The judge provider and model come from
  `ragas_settings.evaluator_provider|evaluator_model`, falling back to
  `services.benchmarking.provider|model` (`service_benchmark.py:1612-1624`).
  `ResultHandler.handle_results` builds each arm's entry and records
  `ragas_effective_settings` only when `ragas_ran` (`service_benchmark.py:414-609`).
- ragas is **absent** from the unit-test environment; `tests/unit/test_benchmark_ragas_dialect.py`
  stubs it through `sys.modules`. `handle_results` tests live in
  `tests/unit/test_benchmark_report_records_running_config.py`.

## Goals / Non-Goals

Goals: every QA row that made an LLM call records the tokens that call reported, attributed
to `(provider, model)`; phase totals in `summary.json`; per-arm judge totals in the
benchmark artifact; old artifacts still load. Non-goals: see proposal (no dollars, no
`compare_runs` block, no SUT usage, no provider edits).

## Decisions

### D1 — Our own callback handler, not langchain's `UsageMetadataCallbackHandler`

The stock handler (`langchain_core.callbacks.usage`) drops a message whose
`response_metadata` has no `model_name` — and `HuitBedrockChatModel` sets none, so every HUIT
call would be silently lost, a false zero. Its `get_usage_metadata_callback()` context manager
also registers a new global configure hook on every entry, which leaks in a per-row loop.
So `src/utils/llm_usage.py` defines:

```python
class UsageRecorder(BaseCallbackHandler):
  def __init__(self, provider: str, model: str) -> None: ...
  def on_llm_end(self, response: LLMResult, **kwargs) -> None: ...
  def snapshot(self) -> Optional[Dict[str, Any]]: ...

def message_usage(message: Any) -> Optional[Tuple[int, int]]: ...
def sum_usage(usages: Iterable[Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]: ...
```

- `on_llm_end` walks **every** generation in `response.generations` (all lists, not only
  `[0][0]`). Each inner list is **one call**: its candidates (`n > 1`) share the
  request-level usage, so only the first `ChatGeneration` in the list that reports
  usage is counted. A list where no candidate reports counts as one unreported call.
- Model attribution per message: `response_metadata.get("model_name")`, else
  `response_metadata.get("model")`, else the constructor's `model`. Provider is always the
  constructor's `provider` (the call site knows it; messages do not carry it).
- A lock guards the totals (ragas runs judge calls concurrently).
- The handler is passed **explicitly** (`config={"callbacks": [recorder]}` or the
  `callbacks=` argument), never through a global hook or contextvar.

### D2 — `message_usage`: what counts as reported

Returns `(input_tokens, output_tokens)` or `None` (not reported). Order:
1. `message.usage_metadata` with `input_tokens` and `output_tokens`.
2. Else `response_metadata["usage"]` or `response_metadata["token_usage"]`, with keys
   `input_tokens`/`output_tokens`, or the OpenAI spelling `prompt_tokens`/`completion_tokens`.
3. Else `None`.

A value counts only if it is an `int`, not a `bool`, and `>= 0`. If either count fails that
test the call is **not reported** — never coerced, never guessed as 0. A call that is not
reported still counts in `calls` and increments `unreported_calls`.

### D3 — The `usage` object (one shape everywhere)

```json
{
  "input_tokens": 1234,
  "output_tokens": 56,
  "calls": 2,
  "unreported_calls": 0,
  "by_model": [
    {"provider": "huit_bedrock", "model": "claude-x", "input_tokens": 1234,
     "output_tokens": 56, "calls": 2, "unreported_calls": 0}
  ]
}
```

- Top-level counts are the sums over `by_model`. `by_model` is sorted by
  `(provider, model)` so the output is deterministic.
- `snapshot()` returns `None` when `on_llm_end` never ran (no LLM call finished). A call that
  raised before `on_llm_end` leaves no trace; that is recorded as a known limit in the docs.
- `sum_usage` merges `by_model` entries on `(provider, model)` and returns `None` when every
  input is `None` (or the input is empty). It skips `None` inputs. It does not validate
  foreign shapes beyond what it needs; a non-dict input raises `ValueError`.
- No `total_tokens` key (the reader adds; one less field to disagree).
- The issue's smoke check `r["usage"]["input_tokens"] > 0` reads the top level; the
  `provider`/`model` requirement is met by `by_model`.

### D4 — QA evaluator runtime exposes `last_usage`

`LangChainEvaluatorRuntime` keeps the per-component `(provider, model)` from the profile
descriptors. `_structured` gains the component's identity, builds a fresh `UsageRecorder`,
passes it as `structured.invoke(messages, config={"callbacks": [recorder]})`, and sets
`self.last_usage = recorder.snapshot()` in a `finally:` — so a call that returned
unparseable output still records its tokens. `self.last_usage` is reset to `None` at the
start of each `_structured` call. This mirrors `ArchiAgentRuntime.tool_calls` (state read by
the caller right after the call). Evaluator runtimes are already one per worker thread
(`phases.score_attempts` `worker_state`), and prepare is serial, so no cross-thread read.

`GoldExtractor` / `AnswerComparator` protocols are unchanged. Call sites read
`getattr(runtime, "last_usage", None)`, so test fakes without the attribute keep working.

### D5 — QA rows

- `prepare_dataset_item`: only when it called `extractor.extract_gold` (`atom_source`
  would be `"inferred"`), read `last_usage` right after the call — in the success path and in
  the `except` path when the extractor was called. `PreparationRecord` gains
  `usage: Optional[Dict[str, Any]] = None`; `to_dict` writes `"usage"` **only when not None**
  (a supplied-atoms row stays byte-identical to today). Skipped records must keep
  `usage is None` (extend the existing "skipped preparation cannot contain output" guard).
- `_record_from_row` accepts `usage` as an **optional** key on `prepared` and
  `preparation_failed` rows (same pattern as `oracle_calls` on failed rows) and round-trips
  it into the record. It checks only that `usage` is a dict or null.
- `run_attempt`: `ArchiAgentRuntime.run` adds a `UsageRecorder(default_provider,
  default_model)` to the callbacks list next to `timing_callback` and sets `self.usage` in
  the same `finally` that sets `self.tool_calls`. `run_attempt` writes
  `"usage": getattr(runtime, "usage", None)` on both `answer_ready` and `execution_failed`
  rows. The key is always present on new answer rows (null = the runtime reported nothing).
- `score_answer`: writes `"usage": getattr(evaluator, "last_usage", None)` on `scored` and
  `evaluation_failed` rows. The `execution_failed` pass-through row in `score_attempts` made
  no judge call and gets **no** `usage` key.

### D6 — Phase totals in `summary.json`

A pure helper (in `llm_usage.py`, e.g. `phase_usage_totals(preparation_rows, answer_rows,
result_rows)`) returns `{"prepare": sum_usage(...), "run": ..., "score": ...}`, each reading
the `usage` key of every row (absent = `None`). Both summary-provenance sites in
`workflow.py` (score ≈`:862`, retry ≈`:1209`) add `"usage": <that dict>` to
`summary["provenance"]`. The totals describe the rows **present in this run directory**; a
retry directory's rows copied from its parent carry the parent's usage, so the retry total
includes them. The docs say so. Keep `workflow.py` edits to the two call sites plus file
reads; logic lives in the helper.

### D7 — Benchmark judge usage

- Factor the judge identity out of `get_ragas_llm_evaluator` into a small pure function
  (e.g. `ragas_judge_identity(config) -> Tuple[str, str]`, same fallback rules, provider
  lower-cased as the `match` does) so the recorder and the model builder cannot disagree.
- `get_ragas_results` builds one `UsageRecorder(provider, model)` per call, passes
  `callbacks=[recorder]` to **every** `evaluate(...)` in `score_fn`, and stores
  `self._judge_usage = recorder.snapshot()` after scoring (also when a metric raised —
  use `try/finally`).
- The run loop sets `self._judge_usage = None` before each arm's `_process_config` and passes
  `judge_usage=getattr(self, "_judge_usage", None)` to `ResultHandler.handle_results`.
- `handle_results` gains keyword `judge_usage: Optional[Dict[str, Any]] = None` and records
  `"judge_usage": judge_usage if ragas_ran else None` on the arm entry — always present on
  new artifacts, null when no judge ran (same absent-vs-null rule as
  `ragas_effective_settings`).

### D8 — Test seams

- `UsageRecorder` is tested by calling `on_llm_end` with hand-built `LLMResult` /
  `ChatGeneration(message=AIMessage(...))` objects — no network, no provider.
- `LangChainEvaluatorRuntime` accepts `model_factory` already; a fake chat model whose
  `with_structured_output(...).invoke(messages, config=...)` calls
  `config["callbacks"][0].on_llm_end(...)` exercises D4.
- `ArchiAgentRuntime` accepts `pipeline_class`; a fake pipeline whose `invoke` calls
  `on_llm_end` on each passed callback that has it exercises D5.
- The ragas stub pattern in `tests/unit/test_benchmark_ragas_dialect.py` lets a fake
  `evaluate` call `callbacks[0].on_llm_end(...)`.

## Risks / Trade-offs

- **A provider that reports no usage** (some vLLM builds) yields `unreported_calls > 0` and
  zero tokens for that model. That is honest; a guessed number would not be.
- **Calls that raise before completion** are invisible (no `on_llm_end`). Documented.
- **Structured-output retries inside langchain** each fire `on_llm_end`, so they are counted
  — correct for cost.
- `service_benchmark.py` is 2746 lines; keep its diff to the identity helper, the recorder
  wiring, and one `handle_results` field.
