## Why

PR #585 (issue #582) added `UsageRecorder` (`src/utils/llm_usage.py:42`), a LangChain
callback handler that counts the tokens each LLM call reports and keys them by
`(provider, model)`. The QA runtime gives one recorder to each question
(`src/evaluation/qa/runtime.py:429-436`). Three gaps make the records incomplete or
wrongly attributed (issue #597; anchors re-verified on `origin/dev` `6b8afe98`):

1. **Calls that skip the caller's callbacks.** `BaseReActAgent.invoke()` puts the
   caller's `callbacks` only into the main `self.agent.invoke` config
   (`src/archi/pipelines/agents/base_react.py:417-421`). Four other LLM calls on the
   same invoke path pass no callbacks, so the recorder never sees them:
   - the recursion-limit wrap-up, `_generate_wrap_up_message` (`:2322`, call at
     `:2335`), reached from `invoke()` through `_handle_recursion_limit_error`
     (`:435`, `:2250-2265`); its async sibling `_generate_wrap_up_message_async`
     (`:2354`, calls at `:2368` and `:2375`) is the same;
   - the context-overflow retry in `_handle_context_overflow` (`:2162`), which calls
     `self.agent.invoke(trimmed_inputs, {"recursion_limit": 10})` (`:2196-2197`);
   - the history summarizer `_summarize_messages` (`:1935`, call at `:1949`), reached
     from `invoke()` through `_prepare_agent_inputs` (`:412`, `:1654`) and
     `_compress_history` (`:1713`, `:1908`). The issue asked to check whether `:1949`
     is the overflow retry: it is not, it is this summarizer. The issue's "Start here"
     step makes every call without callbacks the scope, so it is in scope.
2. **A retry summary repeats the parent's usage.** `_retry_with_open_parent`
   (`src/evaluation/qa/workflow.py:934`) copies the parent's `preparation.jsonl`
   (`:1015-1023`), and its summary sums `usage` over the copied file (`:1242-1246`). The
   retry made no extractor call, but `usage.prepare` repeats the parent's extractor
   tokens.
3. **Every call gets the default provider label.** The recorder labels each call with
   the provider given to its constructor, `services.chat_app.default_provider`
   (`llm_usage.py:45-47`, `:70`). The model comes from the response
   (`_message_model`, `:72-76`), but the provider does not. A pipeline that declares
   `archi.pipeline_map.<Pipeline>.models` on two providers records both models under the
   default provider. Codex raised this on PR #585 (discussion r4153215754).

### Corrections and widenings to the issue body

1. **Gap 2 is wider than `prepare` (widened on purpose).** The retry also writes the
   parent's answer row for every attempt it does not re-run
   (`workflow.py:1131-1147`, `answer or parent_store.get_answer(attempt_id)` at `:1143`) and the
   parent's result row for every attempt it does not re-score (`:1219-1221`,
   `retried_result(...) or result`). Those rows carry the parent's `usage`, so
   `usage.run` and `usage.score` repeat parent tokens in the same way. The issue's own
   rule ("adding the parent and retry summaries double-counts") applies to all three
   phases, and `docs/docs/evaluation.md:1293-1294` already names all copied rows as the
   limit. This change applies one rule to all three phases: a retry summary counts only
   the calls the retry made. The existing test that pins the old totals
   (`tests/unit/evaluation/qa/test_workflow.py:2205`) changes with it.
2. **Classic pipelines cannot be built on `dev`.** `BasePipeline._init_llms`
   (`src/archi/pipelines/classic_pipelines/base.py:62`) calls `get_model(provider,
   model)` with two arguments (`:81`, `:85-87`, `:101`), but `get_model` requires
   `provider_config` (`src/archi/providers/__init__.py:242-244`, required since
   `5e125c56`). Reproduced: both the default path and the declared-models path raise
   `TypeError: get_model() missing 1 required positional argument: 'provider_config'`.
   Gap 3's example (an OpenAI condense model and an Anthropic chat model, the classic
   `QAPipeline`) and its acceptance criterion cannot hold until a classic pipeline can be
   built, so the fix is part of gap 3. The classic path gets the same provider config the
   agent path builds, from one shared function.
3. **No agent tool builds its own LLM on `dev`.** The issue also names "tool-internal LLM
   calls". `grep` finds no `get_model` or chat model in
   `src/archi/pipelines/agents/tools/`. Tagging inside `get_model` (design D4) covers a
   future tool that builds its model through `get_model`; nothing else changes for tools.

## What Changes

- **Gap 1** (`base_react.py`): `invoke()` forwards its `callbacks` to the history
  summarizer, the overflow retry, and the wrap-up call. The wrap-up methods (sync and
  async), `_handle_recursion_limit_error`, `_handle_context_overflow`,
  `_compress_history`, and `_summarize_messages` take an optional keyword-only
  `callbacks` and pass it on. `stream()` and `astream()` take no callbacks today and stay
  unchanged.
- **Gap 2** (`workflow.py`): the retry summary's `provenance.usage` sums only the rows the
  retry produced: `prepare` from no rows (`null`), `run` from the re-run answers, `score`
  from the re-scored results. A normal (non-retry) run is unchanged.
- **Gap 3**: `get_model` tags each chat model it builds with
  `metadata["archi_provider"]` and `metadata["archi_model"]`. `UsageRecorder` reads the
  tag in `on_chat_model_start` / `on_llm_start`, keeps it by `run_id`, and uses it in
  `on_llm_end`. A call with no tag keeps the constructor's provider and model. The classic
  `_init_llms` passes the provider config, built by a new shared
  `build_provider_config` in `src/archi/providers/__init__.py`;
  `BaseReActAgent._build_provider_config` delegates to it.
- **Docs**: `docs/docs/evaluation.md` "Price a run from recorded tokens": replace the two
  limit bullets (retry rows, provider label) with the new rules.
- The usage record schema (`input_tokens`, `output_tokens`, `calls`, `unreported_calls`,
  `by_model[]`) does not change. No key is added.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `qa-evaluation-trial`: the token-usage requirements from `fix-issue-582-eval-token-usage`.
  That change is not archived, so its requirements are not in
  `openspec/specs/qa-evaluation-trial/spec.md`; this change uses `ADDED` deltas.

## Impact

- `src/archi/pipelines/agents/base_react.py`, `src/evaluation/qa/workflow.py`,
  `src/utils/llm_usage.py`, `src/archi/providers/__init__.py`,
  `src/archi/pipelines/classic_pipelines/base.py`, `docs/docs/evaluation.md`, and tests.
- A retry summary's `provenance.usage` values change: they drop the copied rows.
  Consumers: none in `src/` read `provenance.usage` (only the docs describe it).
- A model built by `get_model` now carries two metadata keys. LangChain merges a model's
  own metadata into its callback events only; it does not change the request to the
  provider.
- Out of scope: models built outside `get_model` (the chat app's request-time override at
  `src/interfaces/chat_app/app.py:1680`, the BYOK resolver) keep the constructor label;
  `stream()` / `astream()` callbacks; the console's atom-draft retry
  (`src/evaluation/qa/catalog.py:1131`), which does not write a run summary.
