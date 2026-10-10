## Context

`LlamaIndexHierarchicalRetriever._get_relevant_documents`
(`src/data_manager/vectorstore/retrievers/hierarchical_retriever.py:220-279`) does five steps:
1. Gathers a hybrid-search pool of child chunks.
2. Reranks the children with FlashRank on child text (`_rerank`, `:197-218`).
3. Walks the children in reranked order, keeping the first (best) child per `parent_id`.
4. Fetches the parent texts (`_fetch_parents`).
5. Returns up to `num_documents_to_retrieve` (default 5) parent `Document`s whose `page_content` is the parent text.

The best child per parent is known during the walk (step 3), and it is then thrown away.

The `search_vectorstore_hybrid` tool (`src/archi/pipelines/agents/tools/retriever.py`,
`create_retriever_tool` → `_format_documents_for_llm`) renders `docs[:max_documents]`. Each document
gets a header (title, URL, hash, score) and `page_content.strip()[:max_chars]`. The defaults are
`max_documents=4` and `max_chars=800`, and the whole result is clamped to 8000 characters
(`DEFAULT_RETRIEVER_RESULT_CHARS`). Two other places depend on that clamp:
- the context-editing ceiling, `DEFAULT_PER_RESULT_TOKENS = 2100` (`utils/context_budget.py:160`,
  pinned by `test_ceiling_sits_above_both_source_clamps`);
- the dev exemption arithmetic in archi-config (`(budget + keep) × 2100`).

Measured on dev: parent sections are 5–8k characters; child chunks have median 1433, p90 2014, max 3347 characters. All 7105 children occur verbatim in their parent text. A header averages ~145 characters.

**Prototype (read-only, inside `chatbot-dev`, deployed formatter fed the spans).** The script `render_proto.py` replays the retriever's walk, records each best child's span, and renders twice: as today (parent[:800], 4 documents) and as proposed (span[:1200], 5 documents), each through the 8000 clamp.

| Query | Today | Proposed |
|---|---|---|
| "How do I transfer large data to FASSE?" | 3783 chars, 4 headers, no Globus heading | 6724 chars, 5 headers, "Large Data Transfer with Globus" present |
| "what is the best way to move large files onto FASSE?" | 3759 chars, 4 headers | 6694 chars, 5 headers |

Every span was found, and neither proposed output was clamped. For the FASSE transfers page the span starts at character 1852 of the parent, and the heading is at 2317. For the second query the transfer pages are not in the top 5 at all; that is a ranking problem outside this change.

## Goals / Non-Goals

**Goals:**
- The model reads the passage that ranked each result, not the parent's opening.
- The model reads every result the retriever returns at its default top 5, the same set the user is shown as sources.
- The per-result size bound (8000 characters ≈ 2011 tokens) and everything derived from it stay unchanged.
- `page_content` stays the parent text, so source links, `source_documents` and RAGAS contexts are unchanged.

**Non-Goals:**
- Changing ranking: weights, pool size, top N (#498) or the reranker model.
- Changing the forced-retrieval budget (separate change, #668 / #669).
- Persisting spans at ingest time, or any schema or re-ingest.
- Changing what `HybridRetriever` returns.

## Decisions

**D1: record the span as two integers computed at query time.** During the walk, keep the best child's text per parent. After `_fetch_parents`, compute `start = parent_text.find(child_text)`. When `start >= 0`, set `metadata["matched_start"] = start` and `metadata["matched_end"] = start + len(child_text)`.

Alternatives considered:
- (a) Carry the child text in metadata. It duplicates text already in `page_content`, and `service_benchmark.py:2375-2383` copies every source's full metadata into result files, so each run would grow by roughly 52 questions × ~6 sources × ~1.5 kB ≈ 0.5 MB.
- (b) Persist offsets at ingest. This needs a schema change and a re-ingest. The chunk table's `start_char`/`end_char` are not read by the retriever and are not parent-relative.
- (c) Return the child as `page_content`. This breaks the spec scenario "child hit returns parent context" and changes source content and RAGAS contexts.

`str.find` over five ≤8k-character parents costs microseconds. If the text is not found (none of the 7105 children today), no span is set, and rendering falls back to today's behaviour.

**D2: render the span itself, truncated to `max_chars`; no centred window.** The median child (1433 characters) already exceeds the 1200-character limit, so a window around it would have nothing to add. Rendering from the span start keeps the heading that usually opens a child. Example: the FASSE child begins 466 characters before "Large Data Transfer with Globus", so 1200 characters show the heading plus ~700 characters of that section. Title and URL stay in the header, so the parent's breadcrumb is not needed for citation.

**D3: fixed defaults 5 × 1200, the 8000 clamp unchanged.** 5 × (145 + 1203) + separators ≈ 6.7k characters, which leaves ~1.2k characters of headroom for long titles and URLs before the clamp marks a cut. The alternative was an adaptive per-document share (`max_result_chars / n` minus a header allowance). It handles any top N, but it adds a computation whose output depends on metadata length, and only #498 would ever exercise it. Deferred (see Open Questions). The clamp is not raised: raising it would break the ceiling relationship that `test_ceiling_sits_above_both_source_clamps` pins and would invalidate the archi-config exemption derivation.

**D4: validate the span in the formatter.** Metadata can come from other retrievers or be edited downstream. The formatter uses the span only when both values are non-boolean integers with `0 <= start < end <= len(page_content)`. Otherwise it renders as if no span were present. It strips the rendered slice as today.

**D5: CMS agent unchanged apart from the defaults.** `HybridRetriever` returns `(Document, score)` tuples of chunks with no span. With the new defaults it renders 5 chunks of up to 1200 characters instead of 4 × 800, under the same clamp.

## Risks / Trade-offs

- [Each result grows from ~3.8k to ~6.7k characters; with the forced round plus two model searches, a turn holds ~20k characters of retrieval] → still ≤ 2100 tokens per result, the bound the context-editing arithmetic already assumes for every result. Dev's 32k window was sized for full-size results.
- [Token density of the new shape differs from the 4 × 800 fixture in `test_context_budget.py`] → longer bodies per header lower the density (headers are the dense part), so the margin test should pass more easily. The fixture moves to 5 × 1200 so the test keeps describing the real shape; if it fails, stop and revisit D3.
- [The parent's opening breadcrumb is no longer shown] → the header carries title and URL, and `fetch_catalog_document` remains for full text.
- [Spans past 1200 characters of a long child (p90 2014) are still cut] → strictly better than today's parent[:800]. Tune `max_chars` only with golden-set evidence.
- [Golden-set comparability] → agent behaviour changes, so runs after this land are a new era; note it in the report.
- [#498 (top N 5 → 8) adopted later] → with fixed defaults the tool still shows only 5, and the clamp would cut a sixth anyway. Raising top N therefore needs a matching change here (see Open Questions).

## Migration Plan

No configuration, schema or data changes. Ships with the next dev redeploy, after which the ~65-minute re-ingest window applies. Rollback is reverting the PR.

Verification after deploy: run `live_retriever.py` through the tool wrapper inside `chatbot-dev` for "How do I transfer large data to FASSE?". The output must contain "Large Data Transfer with Globus" and five `[n]` headers, and must be ≤ 8000 characters.

## Open Questions

- If #498 raises top N, should the per-document share become adaptive (D3 alternative), or should `max_documents` follow the configured top N with `max_chars` reduced to fit? Decide in #498, with golden-set evidence.
