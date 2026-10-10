## Why

The hierarchical retriever ranks small child chunks but returns their larger parent sections. The `search_vectorstore_hybrid` tool then shows the model only the **first 800 characters of each parent**, and only **4 of the 5** results (`create_retriever_tool` defaults `max_documents=4`, `max_chars=800`; `_format_documents_for_llm` renders `page_content[:max_chars]`, `src/archi/pipelines/agents/tools/retriever.py:67-123`). The passage that made a result rank is usually outside that window. The model is handed the right page but reads its breadcrumb and intro, and it never sees the fifth result at all, although the user is shown its link.

Measured on dev (complete index, 7105 child chunks):
- **FASSE transfers page** (`kb/fasse-protected-data-transfers`, document 296): the parent section is 5122 characters. Its matched child (chunk 1, 1981 characters) contains "Large Data Transfer with Globus" at character 466, which is character 2312 of the parent. For "How do I transfer large data to FASSE?" this page ranks #2 (rerank 0.99), and the model still reads only the VPN/SFTP preamble.
- **Forced result of conversation 60:** 3792 characters, i.e. 4 × (800-character body + ~145-character header). The fifth document was cited to the user but never shown to the model.
- **Child chunks:** median 1433 characters, p90 2014, max 3347. All 7105 appear verbatim in their parent's text.

## What Changes

- **The retriever records where the match is.** For each returned parent, the hierarchical retriever records the character span of its best-ranked child inside the parent text, as metadata `matched_start` / `matched_end`. They are two integers, not the child text, because the benchmark persists full source metadata into its result files (`src/bin/service_benchmark.py:2375-2383`). The span is omitted when the child text is not found in the parent, and for children without a parent (passthrough rows).
- **The tool shows the match.** `_format_documents_for_llm` renders the matched span when a valid span is present, and falls back to the start of `page_content` as today. Truncation to `max_chars` is unchanged in kind.
- **The tool shows every result, a little longer.** `create_retriever_tool` defaults change from `max_documents` 4 → 5 (the retriever's default top 5, so the model reads every page the user is shown) and `max_chars` 800 → 1200, since most matched spans are 1.4–2k characters. Five results come to about 5 × (145 + 1203) ≈ 6.7k characters. That stays under the **unchanged** 8000-character result clamp, so `DEFAULT_PER_RESULT_TOKENS = 2100` and the context-editing arithmetic stay valid. A read-only prototype against the live dev index measured **6724** characters for "How do I transfer large data to FASSE?". That output shows "Large Data Transfer with Globus"; today's 3783-character output does not. For "what is the best way to move large files onto FASSE?" it measured 6694 characters. Neither output was clamped.
- **Stale comments and fixtures.** Comments and the test fixture that describe a result as "4 documents × 800 chars" (`retriever.py:103-108`, `local_files.py:484`, `tests/unit/test_context_budget.py` `_retrieval_result`) move to the new shape.
- **Unchanged:** `page_content` of returned documents is still the parent text, so source links, `source_documents`, and RAGAS contexts are unchanged. `HybridRetriever` (CMS agent) returns chunks directly with no span, so it only picks up the new defaults. No new configuration keys.

## Capabilities

### New Capabilities
- `retriever-tool-rendering`: what the `search_vectorstore_hybrid` tool shows the model for each retrieved document: which part of the text, how many documents, and the per-result size bound.

### Modified Capabilities
- `hierarchical-rerank-retrieval`: returned parent documents additionally carry the span of the child chunk that ranked them (an added requirement; the existing requirements are unchanged).

## Impact

- **Code:** `src/data_manager/vectorstore/retrievers/hierarchical_retriever.py` (`_get_relevant_documents`), `src/archi/pipelines/agents/tools/retriever.py` (`_format_documents_for_llm`, `create_retriever_tool` defaults, comment), `src/archi/pipelines/agents/tools/local_files.py` (comment only).
- **Tests:** `tests/unit/test_hierarchical_retriever.py` (span recorded and correct; best-ranked child wins on dedup; omitted when not found or for passthrough rows), `tests/unit/test_retriever_llm_context_format.py` (span rendered; fallbacks for absent or invalid spans), a tool-level test that 5 documents render 5 headers within the clamp, and the `test_context_budget.py` realistic-result fixture moved to 5 × 1200.
- **Model input:** each search result grows from ~3.8k to ~6.7k characters (≤ 8000 clamp, ≤ 2100 tokens), and the bodies become the matched passages. This changes agent behaviour, so golden-set RAGAS runs after this lands are a new comparison era. RAGAS contexts themselves are unchanged.
- **Coupling:** #498 proposes raising the reranker's top N from 5 to 8. With fixed per-document defaults, documents 7–8 would be cut by the 8000-character clamp. See design, Open Questions.
- **Deploy:** needs a dev redeploy, which opens the ~65-minute re-ingest window tracked separately.
