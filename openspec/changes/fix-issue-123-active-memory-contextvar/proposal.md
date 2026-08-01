## Why

`BaseReActAgent.start_run_memory()` writes the run's `RunMemory` onto `self._active_memory`
of the **shared** pipeline instance, and the Flask chat app serves requests on threads
(`threaded=True`). Two concurrent **default** (non-overridden) requests therefore race on
that single field: the retrieved documents, tool inputs, and tool-budget accounting of one
request can be silently attributed to the other's answer. Issue #86 closed this bug class
only for the **overridden**-request path (which gets its own `copy.copy` request-local view);
the memory dimension on the shared default path stayed open. This is a P2 correctness bug —
wrong sources cited, wrong budget charged, with no error surfaced.

## What Changes

- Move the per-run active memory off the shared instance attribute and into a module-level
  `contextvars.ContextVar[Optional[RunMemory]]`. `start_run_memory()` sets it for the current
  request context (`ContextVar.set`); the `active_memory` property reads it (`ContextVar.get`).
  Because every read already routes through the `active_memory` property (~30 call sites), the
  tool callbacks `_store_documents`, `_store_tool_input`, and `_consume_tool_budget`
  automatically resolve the **calling** request's memory rather than a specific instance's.
- A `ContextVar` resolves correctly for both the threaded (`stream`) and async (`astream`)
  paths, so no per-request agent recompile is introduced on the default path.
- Now that the static-tool callbacks no longer close over an instance's `_active_memory`, the
  request-local view's `_static_tools` rebuild is no longer required **for memory isolation**.
  The view continues to rebuild only what the LLM/sources override genuinely needs (its
  `agent_llm`, retriever/vector tools); the memory-driven rebuild is removed.
- Preserve all existing behavior of #86 (overridden-request isolation) and of single-threaded
  `invoke()`.

## Capabilities

### New Capabilities
- `concurrent-request-memory-isolation`: Concurrent default (non-overridden) requests served
  by the shared pipeline each keep their own run memory (retrieved documents, tool inputs,
  tool-budget accounting) with no cross-attribution, via a request-context-scoped active
  memory rather than a shared instance attribute — and without per-request agent recompilation.

### Modified Capabilities
<!-- The #86 capability (request-local-llm-override) is not yet archived into openspec/specs/,
     so it cannot be modified via a delta here; its guarantees are preserved and re-asserted
     as scenarios under the new capability. No archived spec's requirements change. -->

## Impact

- **Code:** `src/archi/pipelines/agents/base_react.py` (module-level `ContextVar`,
  `start_run_memory`, `active_memory` property; tool callbacks resolve memory via the
  property). `src/interfaces/chat_app/app.py` (`_build_request_local_pipeline` drops the
  memory-driven `_static_tools` rebuild while keeping LLM/sources override rebuilds).
  Verify tool wiring in `src/archi/pipelines/agents/fasrc_docs_agent.py` and
  `src/archi/pipelines/agents/cms_comp_ops_agent.py` still binds correctly.
- **APIs / behavior:** No public API or config change. Single-threaded `invoke()` semantics
  unchanged. Follow-up to #86; PR references #123 and #86.
- **Dependencies:** None new — `contextvars` is in the standard library.
- **Risk:** The `_static_tools`-rebuild removal must not weaken #86's sources-override
  isolation (a shared static tool bound to the source's retriever). Gated behind a test.
