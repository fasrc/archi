## 1. Layer 2 — retriever context formatter (the critical fix, TDD)

- [x] 1.1 In `tests/unit/` for the retriever tool, add a failing test that builds Documents
  with `metadata = {url, title, resource_hash, filename}` and asserts
  `_format_documents_for_llm` output contains the `title` and the `url` for each snippet
  (and still the hash). Confirm it fails on current code (header is `[idx] {filename} (hash=…)`).
- [x] 1.2 Add a test for the fallback: a Document with `url` but no `title` → the snippet uses
  `display_name` (else filename) as the title text; the hash is never used as citation text.
- [x] 1.3 Implement: change the header in `_format_documents_for_llm`
  (`src/archi/pipelines/agents/tools/retriever.py`) to surface
  `title = title or display_name or filename` and the `url`, retaining `hash`.

## 1b. Retrieval overlay — surface title at query time across ALL paths (review-driven, TDD)

> Codex review: the formatter can only cite what retrieval puts in `doc.metadata`. `title`
> lives in `documents.extra_json`, so every path that builds a Document from a `documents`
> row must overlay it (like `url`), else backfilled titles never reach the model.

- [x] 1b.1 `postgres_vectorstore.py`: extract `_merge_row_metadata` (chunk metadata + the
  `resource_hash/display_name/source_type/url` overlays + `title` from `extra_json`); SELECT
  `d.extra_json` in both `similarity_search_by_vector_with_score` and `hybrid_search`.
  Tests: `tests/unit/test_postgres_vectorstore_title_overlay.py`.
- [x] 1b.2 `hierarchical_retriever.py` `_fetch_parents`: SELECT `d.extra_json` and reuse
  `_merge_row_metadata` (then re-apply `parent_id`), so parent-context docs carry title.
  Required: dev runs `hierarchical_rerank.enabled: true`, so parent docs are what the LLM
  cites — without this, the feature is silently bypassed on the live consumer. Tests added to
  `tests/unit/test_hierarchical_retriever.py`. (Adversarial-review [medium], confirmed via an
  exhaustive sweep as the ONLY remaining title-dropping retrieval path.)

## 2. Layer 1 — HTML `<title>` capture at the processing seam (TDD)

> Seam changed during implementation: a new `HtmlTitleProcessor` in
> `src/data_manager/collectors/processing.py` (run before `HtmlToMarkdownProcessor`), not
> `scraper.py`. Same metadata outcome; unit-testable + avoids reformatting churn in the
> large non-black-clean `scraper.py`. See design.md "Layer 1" seam note.

- [x] 2.1 Add a failing test: given an HTML resource whose content has `<title>Foo</title>`,
  `HtmlTitleProcessor().process(resource)` sets `metadata["title"] == "Foo"`. Confirm it fails
  today (the class doesn't exist / no title is set).
- [x] 2.2 Add tests for fallback (`<h1>` then `og:title` then `""`), the no-clobber guard
  (existing non-empty title preserved), non-HTML/bytes passthrough, and pipeline order
  (title captured before markdown conversion). None raise.
- [x] 2.3 Implement `HtmlTitleProcessor` + `_extract_html_title` in `processing.py` and wire it
  before `HtmlToMarkdownProcessor` in `build_persistence()` (gated by `html_to_markdown.enabled`).

## 3. Layer 3 — FASRC Docs prompt

- [x] 3.1 Update `deploy/fasrc-dev/agents/fasrc-docs.md`: replace the "results are numbered
  `[1]`/`[2]`… for citation" guidance with "cite inline as a Markdown link `[title](url)`
  using the title and url shown for the result, placed where a number would go; no bare
  indices in the answer; never fabricate a URL." Include one worked example.
  (Gitignored dev config — not in the PR diff; note the change in the PR body.)
  - FOLLOW-UP (out of scope): this guidance lives only in the gitignored per-deployment
    prompt, so fresh/new deployments inherit no committed citation-style baseline. Add a
    tracked default — refresh `examples/agents/*` (drop the stale "numbered result indices"
    wording) and/or a tracked base-prompt snippet — in a separate change. (Codex PR #53.)

## 4. Verify

- [x] 4.1 `bash scripts/gate.sh` green (diff-cover ≥ 80% on changed lines); no new dependency.
- [ ] 4.2 Redeploy fasrc-dev + re-ingest (one pass) so existing HTML docs get titles; confirm
  `metadata.title` non-empty coverage rises well above the current ~7% (PDF-only) baseline.
- [ ] 4.3 Live check: ask the dev chat a question whose answer cites a doc; confirm the answer
  contains an inline `[title](url)` Markdown link (clickable) and no bare `[n]` index.
