## Context

`BaseReActAgent` is instantiated once and shared across requests. The Flask chat app serves
requests on threads (`threaded=True`). Per-run state (retrieved documents, tool inputs,
tool-budget accounting) lives in a `RunMemory` object. Today `start_run_memory()`
(`base_react.py:114`, called from `_prepare_agent_inputs` at `:1371`) writes that object onto
`self._active_memory` (`:79`, `:117`) of the **shared** instance. Every consumer reads it through
the `active_memory` property (`:120`) — ~30 call sites, including the static-tool callbacks
`_store_documents` (`:1335`), `_store_tool_input` (`:1353`), and `_consume_tool_budget` (`:1762`),
each of which begins `memory = self.active_memory` and fails open when it is `None`.

Because the field is a single shared slot, two concurrent **default** requests overwrite each
other's `_active_memory`: one request's documents/budget can be attributed to the other's answer,
silently. Issue #86 closed this only for **overridden** requests, which are served from a
`copy.copy` request-local *view* (`chat_app/app.py:_build_request_local_pipeline`, `:164`) that
resets `_active_memory` and `_static_tools` and calls `refresh_agent(force=True)` so the view's
static tools bind their callbacks to the view. The default path — served directly by the shared
instance (#86's "The non-override path is unchanged") — was left racing.

## Goals / Non-Goals

**Goals:**
- Concurrent default requests keep isolated run memory (docs / tool-input / budget), no
  cross-attribution.
- No per-request agent recompile on the default path.
- Correct resolution on both the threaded `stream()` and async `astream()` paths, and unchanged
  single-threaded `invoke()` semantics.
- Preserve #86's overridden-request and sources-override isolation.

**Non-Goals:**
- The overridden-request path already isolated by #86 (kept working, not re-architected).
- Golden-set `locked`/`status` scoring gates.
- Any behavior change to `invoke()` beyond memory resolving through the new mechanism.

## Decisions

**D1 — Move active memory into a module-level `contextvars.ContextVar`, not route every request
through a request-local view.** A module-level `_ACTIVE_MEMORY: ContextVar[Optional[RunMemory]]`
(default `None`) holds the current run's memory. `start_run_memory()` calls `_ACTIVE_MEMORY.set(memory)`
instead of assigning `self._active_memory`; the `active_memory` property returns `_ACTIVE_MEMORY.get()`.
*Rationale:* fixes the default race directly with no per-request agent recompile (the recompile cost
of the route-everything alternative was flagged as unmeasured in the #86 design), and a `ContextVar`
resolves per execution context for both the threaded and async paths. *Alternative rejected:* give
every default request its own `copy.copy` view like #86 — correct but pays the per-request rebuild
cost the operator explicitly wanted to avoid.

**D2 — The `active_memory` property is the single seam; do not touch the ~30 read sites.** Every
reader already routes through the property, so redefining the property to read the `ContextVar`
auto-migrates all callbacks (`_store_documents`, `_store_tool_input`, `_consume_tool_budget`) and
the streaming loops. The callbacks no longer close over a specific instance's `_active_memory`;
they resolve the *caller's* memory. Keep the `None` → fail-open contract exactly (`if not memory:
return`).

**D3 — Remove only the *memory-driven* `_static_tools` rebuild on the request-local view; keep the
LLM/sources rebuild.** Once callbacks resolve memory from the `ContextVar`, a *shared* static tool
no longer records into the source pipeline's memory (the reason #86 rebuilt `_static_tools` on the
view per `app.py:170-181`). So the view's `_static_tools = None` + memory-motivated
`refresh_agent(force=True)` is no longer needed *for memory isolation*. The view MUST still rebuild
whatever the **sources/LLM override** genuinely needs — its `agent_llm`, and any retriever-bound
`_vector_tools` that differ per request. The safe scope of removal is exactly the part justified
only by memory binding; anything a differing retriever needs stays. *This is the highest-risk
edit and is gated behind a test (see Risks).*

**D4 — Set semantics, not reset.** `_prepare_agent_inputs` always calls `start_run_memory()` (hence
`.set()`) before any tool runs, so a fresh value is installed at the top of every request; a stale
value from a reused pool thread is overwritten before it can be read. Resetting via the returned
`Token` in a `finally` is optional hardening, not required for correctness on the Flask
`threaded=True` (thread-per-request) model; include it only if it does not complicate the streaming
generators.

## Risks / Trade-offs

- **[ContextVar does not propagate to executor-offloaded tool callbacks on `astream`]** →
  `contextvars` propagate across `await` within a task and are copied by `asyncio.to_thread`, but
  **not** by a bare `loop.run_in_executor`. If LangGraph runs a sync tool on an executor thread
  without copying the context, the callback sees `None`, fails open, and *drops* attribution (a
  regression to "no docs recorded", not "wrong docs"). *Mitigation:* the TDD test MUST drive the
  **real** execution path for both `stream()` and `astream()` and assert documents actually land in
  each request's memory; if async offloading loses the context, wrap the offloaded call with
  `contextvars.copy_context().run(...)` (or bind the memory explicitly for that path).
- **[D3 removal weakens #86 sources isolation]** → a shared static tool bound to the *source's*
  retriever could serve an overridden request the wrong sources. *Mitigation:* keep the
  concurrent overridden-vs-default isolation test from #86 green; only remove the rebuild the test
  proves is unnecessary. If in doubt, leave the view rebuild in place — it is a cost optimization,
  not part of the correctness fix.
- **[Thread-pool reuse retains a stale `ContextVar`]** → under a pooled WSGI server a reused thread
  keeps the prior request's value until overwritten. *Mitigation:* `start_run_memory()` overwrites
  at request start before any tool runs (D4); the fail-open path only matters when genuinely no run
  is active.

## Migration Plan

Pure in-process refactor — no data, config, or API migration; no rollout steps beyond a redeploy.
Rollback is reverting the change. `contextvars` is standard-library (Python 3.7+).

## Open Questions

- Does LangGraph's `astream` execute the archi sync tool callbacks inside the request's context, or
  on an executor thread that drops it? The concurrent `astream` test answers this empirically and
  decides whether the `copy_context().run` wrap is needed.
- Can the entire memory-motivated `refresh_agent(force=True)` be dropped from
  `_build_request_local_pipeline`, or only the `_static_tools = None` reset, given the view still
  overrides `agent_llm` (which itself may require an agent rebuild)? Resolve by keeping the #86
  override tests green.
