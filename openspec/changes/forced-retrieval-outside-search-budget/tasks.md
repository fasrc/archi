## 1. Red tests (write first, watch them fail for the right reason)

- [x] 1.1 `tests/unit/test_forced_retrieval.py`: FASRCDocsAgent built through the real
      `_update_vector_retrievers` (monkeypatched `build_vector_retriever`, budget 2, started run
      memory) — after `_inject_forced_retrieval` the counter is 0; three model-facing tool calls
      give `[pass, pass, refused]`
- [x] 1.2 Same scenario for CMSCompOpsAgent (monkeypatched `HybridRetriever`)
- [x] 1.3 Forced call id registered: `tool_inputs_by_id()` holds the forced id with
      `{"query": Q}`; `resolve_tool_input("X", "search_vectorstore_hybrid", {})` returns `{}`;
      a subsequent `record_tool_input` attaches to the model's own call
- [x] 1.4 Fallback-with-warning scenario when no unbudgeted tool is present (caplog)
- [x] 1.5 Update the `_make_agent` fixture in `test_forced_retrieval.py` to provide the
      unbudgeted tool so the existing cases keep exercising the production path (confirm
      `tests/unit/test_active_memory_contextvar.py` is unaffected — its pending-input assertion
      concerns a fake graph's `fetch_tool`, not the forced round)

## 2. Implementation (minimum to turn 1.x green)

- [x] 2.1 `FASRCDocsAgent._update_vector_retrievers`: build `self._forced_vector_tool` with
      `create_retriever_tool(retriever, name, description, store_docs=self._store_documents)` —
      no `enforce_budget`, no `store_tool_input`
- [x] 2.2 `FASRCDocsAgent._inject_forced_retrieval`: invoke the forced tool (fallback + warning
      when absent); after a successful call, `active_memory.record_tool_call(call_id,
      "search_vectorstore_hybrid", {"query": query})`
- [x] 2.3 Mirror 2.1–2.2 in `CMSCompOpsAgent`
- [x] 2.4 Docstrings: state that the budget counts model-initiated searches and why the forced
      round is built unbudgeted
- [x] 2.5 Fallback path: claim the input the budgeted tool queued before recording the forced call
      (red first: the fallback test asserts no pending input is left)
- [x] 2.6 `_build_request_local_pipeline` resets `_forced_vector_tool` (red first:
      `test_request_local_pipeline.py::test_view_resets_per_run_state`)
- [x] 2.7 Correct the `context_budget.py` comments that said every result past the budget is a
      refusal

## 3. Verification

- [x] 3.1 `python -m pytest tests/unit/test_forced_retrieval.py tests/unit/test_react_agent_tool_budget.py
      tests/unit/test_retriever_tool_budget.py tests/unit/test_active_memory_contextvar.py` green
- [x] 3.2 `bash scripts/gate.sh` green (black/isort, full unit suite, diff-cover ≥ 80 % vs origin/dev)
- [ ] 3.3 After the dev redeploy: Phoenix shows forced + two model `LlamaIndexHierarchicalRetriever`
      spans before any "Search budget exhausted" output; `agent_traces` `tool_start` args equal
      the retriever spans' `input.value`
