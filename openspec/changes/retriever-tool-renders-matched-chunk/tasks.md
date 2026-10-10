## 1. Red tests (write first, watch them fail for the right reason)

- [ ] 1.1 `tests/unit/test_hierarchical_retriever.py`: a child whose text sits at offset S of its
      parent → returned parent has `matched_start == S`, `matched_end == S + len(child)`,
      `page_content` unchanged (reuse `_child`, `_parent_row`, `mock_vectorstore`)
- [ ] 1.2 Same file: two children of one parent, reranker puts B above A → one parent, span locates B
      (`_ReverseRanker` pattern)
- [ ] 1.3 Same file: child text absent from parent text → no span keys; passthrough child (no
      `parent_id`) → no span keys
- [ ] 1.4 `tests/unit/test_retriever_llm_context_format.py`: span starting past char 800 → snippet
      contains the span text and not the preceding text; no span → start of `page_content`
      (existing behaviour); invalid spans (out of range, empty, reversed, float, bool) → start of
      `page_content`
- [ ] 1.5 Tool-level test (in `test_retriever_llm_context_format.py` or a new
      `test_retriever_tool_defaults.py`): `create_retriever_tool` with defaults over a retriever
      returning 5 documents with realistic metadata → 5 headers `[1]`…`[5]`, output ≤ 8000 chars;
      oversized metadata → clamped with the truncation marker (existing #235 behaviour still holds)

## 2. Implementation (minimum to turn 1.x green)

- [ ] 2.1 `hierarchical_retriever.py` `_get_relevant_documents`: keep the best-ranked child's text
      per `parent_id` during the walk; after `_fetch_parents`, set `matched_start`/`matched_end`
      via `parent_text.find(child_text)` when found
- [ ] 2.2 `tools/retriever.py` `_format_documents_for_llm`: use the validated span when present,
      else the start of `page_content`; truncation and `...` marker unchanged
- [ ] 2.3 `tools/retriever.py` `create_retriever_tool`: defaults `max_documents=5`,
      `max_chars=1200`; update the `DEFAULT_RETRIEVER_RESULT_CHARS` comment ("4 documents x 800")
      and the docstring
- [ ] 2.4 `tools/local_files.py:484` comment and `tests/unit/test_context_budget.py`
      `_retrieval_result` fixture → 5 documents × 1200 chars (confirm the margin test still passes;
      if it does not, stop and revisit design D3)

## 3. Verification

- [ ] 3.1 `python -m pytest tests/unit/test_hierarchical_retriever.py
      tests/unit/test_retriever_llm_context_format.py tests/unit/test_retriever_score_from_metadata.py
      tests/unit/test_retriever_tool_budget.py tests/unit/test_context_budget.py
      tests/unit/test_react_agent_tool_budget.py` green
- [ ] 3.2 `bash scripts/gate.sh` green (black/isort, full unit suite, diff-cover ≥ 80 % vs origin/dev)
- [ ] 3.3 Read-only check on the running dev stack, no redeploy: run the worktree's
      `_format_documents_for_llm` over documents from the live retriever (the live retriever
      lacks the span until deployed, so compute the span in the check script the same way 2.1
      does) for "How do I transfer large data to FASSE?" → output contains
      "Large Data Transfer with Globus", 5 headers, ≤ 8000 chars
- [ ] 3.4 After the dev redeploy (not in this change's session): the same query through the deployed
      tool shows the Globus section; note the new golden-set era in the report
