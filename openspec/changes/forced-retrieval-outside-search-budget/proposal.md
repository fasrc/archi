## Why

The forced initial retrieval — the prefilled `search_vectorstore_hybrid` round that guarantees every
chat turn starts from real chunks — is invoked through the *same budgeted tool instance the model
uses*. It therefore spends one of the two per-turn search slots (`DEFAULT_TOOL_BUDGETS
["search_vectorstore_hybrid"] = 2`, `base_react.py:90`), so the model gets exactly **one** search of
its own and every further reformulation is refused with "Search budget exhausted". The same call
also pushes its input into `RunMemory._pending_tool_inputs_by_name` where no streamed tool-call id
ever claims it, so every persisted `tool_start` event carries the *previous* call's arguments.

Measured on dev (conversation 60, 2026-10-10 04:35 UTC, Phoenix spans): forced search with the
verbatim question → the model's one search ("transfer large files FASSE rsync scp best practices")
→ next search refused → answer "the documentation does not appear to contain specific guidance",
although a rephrased query ranks the right page #2. In the Phoenix-retained history every third
search in a turn was refused. The cap (`38a2c55b`, 2026-06-04) predates the forced round
(`6ffd7c8f`, 2026-06-09) and was never resized for it.

## What Changes

- The forced initial retrieval runs through a **second, unbudgeted instance** of the retriever
  tool (same retriever, same `store_docs` callback, no `enforce_budget`, no `store_tool_input`).
  The per-turn budget for `search_vectorstore_hybrid` thereby counts **model-initiated searches
  only**; its default stays 2, so a turn now holds the forced round plus up to two model searches.
- After the forced call, the agent **registers the forced tool-call id and its arguments** in the
  active `RunMemory` (`record_tool_call`) instead of queueing a pending input. Streamed model calls
  then resolve their own arguments, and `agent_traces` `tool_start` events stop being shifted by one.
- Applies to both agents that prefill a forced round: `FASRCDocsAgent` and `CMSCompOpsAgent`.
- **Unchanged on purpose:** the context-editing retrieval exemption stays sized from the budget
  (the earliest 2 `search_vectorstore_hybrid` results — forced + first model search — exactly what
  it protects today). Sizing it to budget + 1 needs an archi-config re-derivation
  (`exemption_fraction ≥ 0.43` at a 32768 window; see design) and is a follow-up.
- No new configuration keys; no change to `DEFAULT_TOOL_BUDGETS`.

## Capabilities

### New Capabilities
- `agent-search-budget`: per-turn accounting of `search_vectorstore_hybrid` calls — which calls
  the budget counts, how the forced initial round is exempted, and how that round is recorded in
  run memory so trace arguments stay aligned with the call that made them.

### Modified Capabilities
<!-- none: no existing spec covers the search budget or the forced round -->

## Impact

- **Code:** `src/archi/pipelines/agents/fasrc_docs_agent.py` (`_update_vector_retrievers`,
  `_inject_forced_retrieval`), `src/archi/pipelines/agents/cms_comp_ops_agent.py` (same two
  methods). No change to `base_react.py`, `tools/retriever.py` or `run_memory.py`.
- **Tests:** `tests/unit/test_forced_retrieval.py` gains FASRCDocsAgent + CMSCompOpsAgent cases
  that drive the forced round through a *real budgeted* tool and assert the forced call's
  registration in run memory; its `_make_agent` fixture learns the unbudgeted tool. No existing
  test encodes the current (buggy) accounting — the forced round has no budget-aware test today.
- **Runtime behaviour:** one more real retriever call per turn is possible (≈10 s steady state on
  dev) and one more ≤8000-char tool result (≤2100 tokens) can sit in context; that third result is
  clearable under context pressure (not exempt).
- **Traces/observability:** `tool_inputs_by_id` gains a `forced_search_*` entry with the real
  query; persisted `tool_start` args become correct.
- **Benchmarks:** changes agent behaviour, so RAGAS golden-set runs after this land are a new era
  for comparison purposes (note it in the report).
- **Deploy:** needs a dev redeploy to take effect (which itself opens the ~65-minute partial-index
  window tracked separately).
