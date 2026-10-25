## Context

`UsageRecorder` (`src/utils/llm_usage.py:42-123`) is a LangChain callback handler. It
counts each finished call in `on_llm_end` and keys the count by `(provider, model)`. The
provider is always the constructor's; the model is the response's `model_name` / `model`,
else the constructor's (`_message_model`, `:72-76`). The QA runtime builds one recorder
for each question with `services.chat_app.default_provider` / `default_model` and passes
it as `pipeline.invoke(..., callbacks=[timing_callback, usage_recorder])`
(`src/evaluation/qa/runtime.py:429-436`). The runtime caches one pipeline for each
runtime and reuses it for later attempts (`runtime.py:392-394`, `:421`).

Facts checked against the installed `langchain_core` 1.2.13 (one throwaway script, not a
repo test):

- A chat model's own `metadata` field reaches `on_chat_model_start(..., metadata=...)`.
  It can be set after construction (`model.metadata = {...}`) on `ChatOpenAI`,
  `ChatAnthropic`, `ChatOllama`, and `GenericFakeChatModel`.
- The `run_id` given to `on_chat_model_start` is the `run_id` given to `on_llm_end`.
- A model bound with `.bind(...)` keeps its metadata. A parent config's `metadata` with the
  same key does not override the model's own value (`chat_models.py:506-518`: config
  metadata is inheritable, the model's metadata is local and merges last).

## Goals / Non-Goals

**Goals:** every LLM call on the `invoke()` path reaches the caller's callbacks; a retry
summary counts only the calls the retry made; each recorded call carries the provider and
model its model was built with; a classic pipeline can be built.

**Non-Goals:** callbacks for `stream()` / `astream()` (they take none today); a label for
models built outside `get_model` (the chat app's request-time override at
`src/interfaces/chat_app/app.py:1680`, the BYOK resolver); a change to the usage record
schema; a change to the row files (`preparation.jsonl`, `answers.jsonl`,
`evaluation_results.jsonl`) of any run.

## Decisions

### D1. Gap 1: pass `callbacks` as an explicit keyword, not as shared state

`invoke()` already holds the caller's `callbacks` (`base_react.py:397-421`). It passes
them on by keyword:

- `self._prepare_agent_inputs(callbacks=callbacks, **kwargs)` (`:412`). Inside,
  `self._compress_history(history_messages, callbacks=kwargs.get("callbacks"))`
  (`:1713`), which passes `callbacks` to `self._summarize_messages(chunk, callbacks=...)`
  (`:1929`).
- `self._handle_recursion_limit_error(..., callbacks=callbacks)` (`:435`), which passes
  it to `self._generate_wrap_up_message(..., callbacks=callbacks)` (`:2260`).
- `self._handle_context_overflow(..., callbacks=callbacks)` (`:452`).

Each LLM call adds the callbacks to its config through one small static helper:

```python
@staticmethod
def _callbacks_config(callbacks: Optional[Sequence[Any]]) -> Dict[str, Any]:
    """The runnable-config entry that forwards a caller's callbacks (#597)."""
    return {} if callbacks is None else {"callbacks": list(callbacks)}
```

- summarizer (`:1949`) and wrap-up (`:2335`):
  `self.agent_llm.invoke([...], config=self._callbacks_config(callbacks))`;
- async wrap-up (`:2368`, `:2375`): the same `config=` on `ainvoke` and on the `invoke`
  fallback;
- overflow retry (`:2196-2197`):
  `self.agent.invoke(trimmed_inputs, {"recursion_limit": 10, **self._callbacks_config(callbacks)})`.

Every new parameter is keyword-only with default `None`. `stream()` and `astream()` keep
their calls as they are, so their behavior does not change: `None` gives an empty config
entry. Test doubles that override `_prepare_agent_inputs(**kwargs)` or
`_generate_wrap_up_message(**kwargs)` (`tests/unit/test_react_agent_context_overflow.py:60`,
`tests/unit/test_base_react_streaming_think_leak.py:124-131`) accept the new keyword.

*Alternatives rejected.* An instance attribute set in `invoke()` leaks callbacks into a
later attempt or a concurrent call on the cached pipeline. LangChain's child-config
context variable is not set any more when `self.agent.invoke` has raised, and the
wrap-up and the overflow retry run in the `except` blocks (`:428-458`), so they would not
inherit it.

`_generate_wrap_up_message_async` gets the same parameter although `astream()` passes
none today: the issue names both siblings, and the two stay symmetric.

### D2. Gap 2: a retry summary counts only the calls the retry made

The rule: **a retry summary's `provenance.usage` counts only the LLM calls that the retry
made.** Then the parent summary plus the retry summary is the total spend, with no
double count.

- `prepare`: the retry copies `preparation.jsonl` and makes no extractor call, so it sums
  no rows and is `null`.
- `run`: the answers the retry re-ran, `parent_store.retried_answer(attempt_id)` for each
  parent attempt, where it is not `None` (`src/evaluation/qa/workspace.py:396-397`).
- `score`: the results the retry re-scored, `parent_store.retried_result(attempt_id)`
  where it is not `None` (`workspace.py:408-409`).

A re-run answer that a later live-validation failure replaces in `answers.jsonl` still
counts: the call was made. Add a small helper in `workflow.py`, for example
`_retry_usage_totals(parent_results, parent_store)`, that returns
`phase_usage_totals([], fresh_answers, fresh_results)`, and use it at `:1242-1246`. The
normal summary at `:887-891` stays as it is.

The row files stay as they are: a copied row still carries the parent's `usage`, which
is the row's own provenance. Only the retry summary's totals change.

*Widening (stated in `proposal.md`).* The issue names only `prepare`. The carried answer
and result rows (`workflow.py:1143`, `:1220`) repeat parent tokens by the same cause, so
the one rule covers all three phases. *Alternatives rejected:* `prepare` only (leaves
`run` and `score` double-counted by the same cause); a per-row `carried` marker (adds a
key to the row schema).

### D3. Gap 3: the recorder reads a per-call label from the model's metadata

Two key names, defined once in `src/utils/llm_usage.py`:
`ARCHI_PROVIDER_KEY = "archi_provider"`, `ARCHI_MODEL_KEY = "archi_model"`.

`UsageRecorder` adds a `self._run_labels: Dict[Any, Tuple[str, Optional[str]]]`, guarded
by the existing lock:

- `on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs)`
  and `on_llm_start(self, serialized, prompts, *, run_id, metadata=None, **kwargs)`: if
  `metadata` has a non-empty string `archi_provider`, store
  `(metadata["archi_provider"], metadata.get("archi_model"))` under `run_id`.
- `on_llm_end(self, response, **kwargs)`: pop the label for `kwargs.get("run_id")`. The
  provider is the label's provider, else the constructor's. The fallback model is the
  label's model, else the constructor's. The response's `model_name` / `model` still wins
  for the model (unchanged precedence), and an unreported call now uses the fallback
  model.
- `on_llm_error(self, error, **kwargs)`: pop the label. A call that raised is not counted
  (unchanged; `docs/docs/evaluation.md` Limits).

A call with no label (a model built outside `get_model`, or an existing test that calls
`on_llm_end` with no `run_id`) keeps the constructor's provider and model, so
`test_provider_always_from_constructor` (`tests/unit/test_llm_usage.py:126`) stays green.

### D4. Gap 3: tag the model in `get_model`, the one factory every path uses

At the end of `get_model` (`src/archi/providers/__init__.py:298-299`), when the result is
a `BaseChatModel`:

```python
model.metadata = {
    **(model.metadata or {}),
    ARCHI_PROVIDER_KEY: provider_type_enum.value,
    ARCHI_MODEL_KEY: model_name,
}
```

Any other return type is returned untagged. The provider label is the canonical
`ProviderType` value (lowercase, for example `openai`, `huit_bedrock`), the same spelling
as a lowercase `default_provider`. `get_model` builds the models of the classic
`_init_llms`, the agent `_init_llms` (`base_react.py:1294`, `:1323`), the RAGAS judge
(`src/utils/ragas_scoring.py:76-102`), and the ingestion model
(`src/data_manager/collectors/processing.py:1238`), so one change covers each path. The
judge's recorder uses the same provider and model the judge was built from, so the
benchmark `judge_usage` requirement holds.

*Alternative rejected:* a tag in each `_init_llms` and each tool factory (four places,
easy to miss one).

### D5. Classic pipelines: build each model with the provider's config

Move the body of `BaseReActAgent._build_provider_config` (`base_react.py:1327-1351`) to a
module function `build_provider_config(provider, providers_config) -> dict` in
`src/archi/providers/__init__.py`, beside `get_model` and `apply_local_mode`. The static
method becomes a one-line delegate, so its callers and its tests
(`tests/unit/test_base_react_local_mode_seam.py`,
`tests/unit/test_chat_sampling_extra_kwargs.py`) do not change.

`BasePipeline._init_llms` (`classic_pipelines/base.py:62-104`) reads
`self.config["services"]["chat_app"]["providers"]` with the same defensive dict checks as
the agent (`base_react.py:1265-1279`) and passes
`build_provider_config(provider, providers_config)` as the third argument of all three
`get_model` calls (`:81`, `:85-87`, `:101`). This removes the `TypeError` that blocks
every classic pipeline on `dev`. Import from the provider layer, not from
`base_react.py`, so the classic module does not import the agent module.

The other copies of this logic (`src/archi/pipelines/agents/utils/thinking_gate.py:44`,
`processing.py:1443`, `app.py:182`) stay as they are; consolidating them is out of scope.

### D6. Tests (each one fails on `origin/dev` and passes on the branch)

- Gap 1, new file `tests/unit/test_base_react_usage_callbacks.py`. Copy the
  `_TestableAgent` stub from `tests/unit/test_react_agent_context_overflow.py:36-62`. Use
  a real `UsageRecorder` and a `GenericFakeChatModel`
  (`langchain_core.language_models.fake_chat_models`) whose messages carry
  `usage_metadata`, as `agent_llm`:
  - `agent.agent.invoke` raises `GraphRecursionError`; after
    `agent.invoke(callbacks=[recorder])`, `recorder.snapshot()["calls"] == 1` (the
    wrap-up). Without callbacks, the wrap-up still runs and returns its answer.
  - `agent.agent.invoke` raises the overflow error once (reuse `VLLM_OVERFLOW_MSG`), then
    returns `{"messages": [AIMessage("ok")]}`; the second call's config has
    `recursion_limit == 10` and `callbacks == [recorder]`.
  - `agent._compress_history(history, callbacks=[recorder])` with more than 4 messages
    counts 1 call; `agent.invoke(callbacks=[recorder])` passes `callbacks` to
    `_prepare_agent_inputs` (record it in the stub's override).
  - `asyncio.run(agent._generate_wrap_up_message_async(..., callbacks=[recorder]))`
    counts 1 call.
- Gap 2, `tests/unit/evaluation/qa/test_workflow.py`: change
  `test_retry_phase_usage_appears_in_summary_provenance` (`:2205`) to the new totals, and
  add a test whose parent has inferred atoms (the `_dataset` helper used by
  `test_phase_usage_appears_in_summary_provenance`, `:2170`, gives extractor usage) and
  one failed attempt (`_AgentFactory(failures=...)`). Assert, for the retry:
  `usage.prepare is None`; `usage.run` equals the re-run answer's usage only;
  `usage.score` equals the re-scored results' usage only; the copied
  `preparation.jsonl` rows still carry their `usage`.
- Gap 3: in `tests/unit/test_llm_usage.py`, a labelled chat run, a labelled unreported
  call, a labelled `on_llm_start` run, an unlabelled run, and an `on_llm_error` that
  drops the label. A new `tests/unit/test_get_model_usage_tag.py`: monkeypatch
  `src.archi.providers.get_provider` with a stub provider whose `get_chat_model` returns a
  `GenericFakeChatModel`; `get_model("OpenAI", "m1", {})` tags `openai` / `m1` and keeps
  existing metadata; a non-`BaseChatModel` result is returned untagged. Then the
  end-to-end case from the issue: a `BasePipeline` (built with `BasePipeline.__new__`,
  then `config`, `pipeline_config`, `default_provider`, `default_model` set) that declares
  `condense_model: openai/m1` and `chat_model: anthropic/m2`; after `_init_llms()`, both
  models are invoked with one `UsageRecorder("local", "default-m")`, and `by_model` has
  the providers `openai` and `anthropic`. The stub also records the provider config it
  received, so the test proves the classic path passes
  `services.chat_app.providers.<provider>` through.

## Risks / Trade-offs

- **A retry summary's totals get smaller.** A reader who compared a retry's totals with
  a full run's totals gets different numbers. The docs state the new rule.
- **The model's `metadata` is visible to every callback handler** (tracing too). The two
  keys hold a provider id and a model id, which the configuration already exposes. They
  do not go into the request to the provider.
- **A model built outside `get_model` keeps the default provider label.** Stated in the
  docs.
