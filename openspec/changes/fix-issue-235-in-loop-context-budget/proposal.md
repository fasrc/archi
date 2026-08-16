## Why

The ReAct agent exhausts the model's context window on 3–5% of goldenset questions and
returns `"I'm sorry, but the conversation history has grown too large for me to process."`
instead of an answer (issue #235). The agent already owns three pieces of context-budget
machinery, and the failure lives in the gap between two of them: the token budget at
`base_react.py:1445-1480` runs **once before** `agent.invoke()` and only over conversation
history, so the `ToolMessage` payloads that accumulate *inside* the LangGraph loop never pass
through the one component that knows the real budget. The reactive catch at
`base_react.py:1877` fires only after the window is already gone.

The dominant accumulator is `fetch_catalog_document`: ~1.05 K tokens per call
(`max_chars=4000` plus a metadata preview) with **no** cap other than `recursion_limit=50`.
Roughly 20–24 calls fill a 32 K window, and the recursion limit permits ~24 tool/model round
trips — the two numbers meet. Retrieval cannot be the driver: its result is hard-capped at
`max_documents=4 × max_chars=800` ≈ 900 tokens per call under a 2-call budget, and
`retriever.py:134-138` passes the *full* document list to `store_docs` while formatting only
the first four for the model, so recorded source counts are decoupled from context cost by
construction (an `ok` benchmark row carried 67 sources; `question_94` overflowed with 18).

This is not only a benchmark problem — `base_react.py` is the agent base class on the
production chat path — but the benchmark is where it is measurable, and it currently makes
that measurement unusable: a degraded row is dropped from the RAGAS denominators, so an arm
that changes retrieval also changes which questions drop out, and the #205 grading gate
cannot distinguish a real improvement from an arm that serves users worse.

## What Changes

- Fill the **unused** `_build_static_middleware` seam (`base_react.py:1378-1380`, currently
  returning `[]` and passed straight to `create_agent(..., middleware=...)`) with a
  context-editing middleware that runs on every model call inside the loop.
- Bound accumulated tool content by **tokens, not call counts**: once the accumulated prompt
  exceeds the budget, the oldest tool results are replaced with an instructive placeholder
  while the N most recent are preserved at full fidelity.
- Evaluate the **complete** request the provider will receive — system prompt and tool schemas
  included, not the conversation messages alone — so the check cannot sit below its threshold
  while the real request exceeds the window.
- Derive the budget from `self._get_model_context_window()` minus the **existing** 15%
  convention already used by the pre-loop budget, documented for what it actually is: a
  generation reserve. `ModelInfo.context_window` is a *total* sequence length covering prompt
  and response, so a budget equal to the full window is exceeded by any answer. No hard-coded
  context length.
- **Enforce a per-result size ceiling on every tool result that survives reduction**, applied in
  the middleware and therefore independent of which tool produced it. Preservation selects by
  recency across all tool results, so the surviving set can include MCP tools loaded at runtime
  and caller-supplied `extra_tools` that this change cannot enumerate — a ceiling enforced only
  on named tools lapses the moment another is enabled.
- Additionally clamp at the source, as defense in depth rather than as the bound:
  `fetch_catalog_document`'s `max_chars` is a model-supplied argument forwarded unclamped to the
  catalog endpoint, where `max_chars=0` disables truncation and returns the whole document; and
  the retriever's `max_chars` bounds only `page_content`, leaving the metadata-derived header
  uncapped, so its clamp applies to the complete serialized output.
- Exempt the retrieval tool's results from clearing **while that exemption is provably cheap**,
  and drop it with a warning when the retrieval caps in force could let exempted content occupy
  too large a share of the budget — otherwise a raised retrieval budget becomes a second
  unbounded floor outside the clearing strategy.
- Add a configuration seam (`services.chat_app.context_editing`) following the established
  three-layer lookup idiom, so the behaviour can be tuned or disabled without a code change.
- **Fail open**: when the context window cannot be determined, emit no middleware and behave
  exactly as today.
- No breaking changes. The reactive `_handle_context_overflow` path and its spec requirements
  are retained unchanged as a last-resort net; this change is what makes reaching it abnormal.

Explicitly **not** changed, and why:

- No entry is added to `DEFAULT_TOOL_BUDGETS` for `fetch_catalog_document`. It would be
  inert: `_consume_tool_budget` is never called by the framework and reaches a tool only via
  an explicit `enforce_budget=` callback, a parameter that exists solely on
  `create_retriever_tool` (`retriever.py:76`). `create_document_fetch_tool` accepts no such
  parameter, so the blind spot is a missing enforcement seam, not a missing config value.
  A call-count cap also cannot bound tokens — one 4000-char read is worth ~20 short ones.
- No change to retrieval scoring, weights, or the goldenset bank; #205 is in flight on the
  same subsystem.

## Capabilities

### New Capabilities

None. The behaviour belongs to the existing agent context-resilience capability.

### Modified Capabilities

- `agent-context-resilience`: adds proactive requirements — the agent MUST reduce tool-content
  accumulation *within* the reasoning loop against a budget derived from the model's context
  window, evaluated over the complete provider request, preserving the most recent tool results
  and the grounding retrieval evidence, with every term of the arithmetic an enforced ceiling
  rather than a default — so the existing reactive overflow path becomes a last resort rather
  than a routine outcome. The existing reactive requirements are unchanged.

## Impact

- **Code**: `src/archi/pipelines/agents/base_react.py` (`_build_static_middleware` becomes a
  thin call site); one new tested helper module under
  `src/archi/pipelines/agents/utils/` holding the budget derivation, the complete-request token
  counter, and middleware construction, so the new logic is unit-testable and reaches the
  diff-coverage floor; `src/archi/pipelines/agents/tools/local_files.py` gains the enforced
  `max_chars` ceiling.
- **Tests**: extends `tests/unit/test_react_agent_tool_budget.py`; new unit tests for the
  helper module and for the clamped document fetch.
  `tests/unit/test_react_agent_context_overflow.py` must continue to pass unchanged.
- **Follow-up filed, not fixed here**: `api_catalog_document`
  (`src/interfaces/uploader_app/app.py:761-770`) honours an unbounded `max_chars` for any
  caller. The agent path is closed by the tool-side clamp; the endpoint clamp is separate
  hardening and does not belong in an agent-context PR.
- **Dependencies**: none added. `langchain` 1.0.3 is already pinned and already provides
  `create_agent(..., middleware=...)`, `AgentMiddleware.wrap_model_call`, and
  `ClearToolUsesEdit`. The dependency is deliberately narrow — the upstream *message rewriter*
  is reused, its trivial middleware wrapper is not — and a contract test pins the two API
  surfaces relied on so a langchain upgrade fails loudly instead of silently disabling the
  bound.
- **Config**: one new optional block under `services.chat_app`; absent config preserves
  current behaviour with the middleware enabled at derived defaults.
- **Runtime**: every model call gains an O(messages) token approximation. No extra provider
  calls, no network I/O.
- **Benchmark**: unblocks the #205 group-6 grading gate, which is stopped pending this fix.
