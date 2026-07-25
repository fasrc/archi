## 1. Failing test — concurrent default-request memory race (RED)

- [x] 1.1 Add a test in `tests/unit/` that runs two **default** (no override) requests concurrently
  through one shared `BaseReActAgent`-derived pipeline instance, each invoking a document-retrieving
  static tool, and asserts each request's `RunMemory` holds only its own retrieved documents, tool
  inputs, and tool-budget consumption (no cross-attribution). Use a stub tool/LLM so the test needs
  no network or deployment. Drive the real `stream()` path (threads).
- [x] 1.2 Run the test and confirm it FAILS on the shared-`self._active_memory` race (documents from
  one request appear in the other's memory). Capture the failure as the red baseline.

## 2. ContextVar fix — move active memory off the shared instance (GREEN)

- [x] 2.1 In `src/archi/pipelines/agents/base_react.py` add a module-level
  `_ACTIVE_MEMORY: contextvars.ContextVar[Optional[RunMemory]]` with default `None` (import
  `contextvars`).
- [x] 2.2 Change `start_run_memory()` (`:114`) to `_ACTIVE_MEMORY.set(memory)` instead of assigning
  `self._active_memory`; change the `active_memory` property (`:120`) to return `_ACTIVE_MEMORY.get()`.
  Remove the `self._active_memory` instance attribute (`:79`) — or leave it unused only if a
  subclass/serialization needs it (prefer removal).
- [x] 2.3 Confirm the static-tool callbacks `_store_documents`, `_store_tool_input`, and
  `_consume_tool_budget` still read `self.active_memory` (the property) and keep the `None` →
  fail-open contract; no callback should reference `self._active_memory` directly.
- [x] 2.4 Run the task-1 test and confirm it now PASSES for the `stream()` path.

## 3. Async path — resolve the caller's memory on `astream()`

- [x] 3.1 Add a test that runs two concurrent **default** requests through the async `astream()`
  path and asserts the same per-request memory isolation as task 1.
- [x] 3.2 Run it. If tool callbacks resolve `None` because LangGraph offloads sync tools to an
  executor that drops the context, wrap the offloaded execution with
  `contextvars.copy_context().run(...)` (or otherwise bind the request's memory into that context)
  until the test passes. If `astream` already resolves correctly, no code change is needed — record
  that in the test.

## 4. Drop the memory-driven request-local-view rebuild (#86 D1a) — guarded

- [ ] 4.1 Ensure the #86 concurrent overridden-vs-default isolation test exists and is green
  (add/port one if missing) so it guards this step.
- [ ] 4.2 In `src/interfaces/chat_app/app.py` `_build_request_local_pipeline`, remove the
  `_static_tools = None` reset and the memory-motivated `refresh_agent(force=True)` **only** to the
  extent they were needed for memory isolation; KEEP everything the LLM/sources override needs
  (`agent_llm`, `_vector_tools`/`_vector_retrievers` reset, and any rebuild a differing retriever
  requires). If removing the rebuild fails the guard test, leave it in place (it is a cost
  optimization, not part of the correctness fix) and note that in the task.
- [ ] 4.3 Run the #86 override isolation tests and the task-1/task-3 tests together; all green.

## 5. Verify subclasses and full behavior

- [ ] 5.1 Confirm `src/archi/pipelines/agents/fasrc_docs_agent.py` and
  `src/archi/pipelines/agents/cms_comp_ops_agent.py` tool construction still binds correctly (their
  tools' callbacks resolve memory via the property / ContextVar); add a lightweight assertion if a
  gap is found.
- [ ] 5.2 Confirm single-threaded `invoke()` records docs / tool-input / budget exactly as before
  (add or extend a test if not already covered).

## 6. Gate and PR

- [ ] 6.1 `bash scripts/gate.sh` exits 0 (black/isort, pytest, ≥80% diff coverage vs `origin/dev`).
- [ ] 6.2 Open a PR against `fasrc/archi:dev` whose body references #123 and #86 and links the
  glossary on first use of project terms; do not merge.
