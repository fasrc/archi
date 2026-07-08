## 1. Boilerplate strip — landmark slice (TDD)

- [x] 1.1 RED: in `tests/unit/test_html_to_markdown_processor.py` (mirror the `_html_resource` factory + inline-HTML style), add a test that a page whose Markdown contains pre-`Table of Contents` nav, a body, then `Bookmarkable Links`/footer converts to Markdown that **keeps the body and drops both ends**. Watch it fail.
- [x] 1.2 RED: add tests for the boundary rules — end falls back to `Last Updated` when `Bookmarkable Links` is absent; when **neither** landmark pair is present the full Markdown is kept unchanged (non-KB page); a slice that would be blank keeps the full Markdown.
- [x] 1.3 GREEN: add module-level landmark constants and `_slice_kb_article(md) -> str` (after `_markdownify_deep_safe`), then call it on the converted Markdown inside `HtmlToMarkdownProcessor.process` before assigning `resource.content`. Confine edits to these seams (design D4).
- [x] 1.4 Confirm the existing conversion tests (deep-nested fallback, blank-output, raise-keeps-original, suffix/path rewrite, hash-unchanged) all still pass.

## 2. Source category capture (TDD)

- [x] 2.1 RED: add `tests/unit/test_html_category_processor.py` (mirror `test_html_title_processor.py`): KB HTML with a `Home › <Category> › <Article>` breadcrumb (`span.eckb-breadcrumb-link`) → `metadata["category"] == "<Category>"`, content/suffix unchanged. Watch it fail.
- [x] 2.2 RED: add guard tests — no breadcrumb / <3 crumbs → no `category`; existing non-empty `category` not overwritten; `bytes`/non-`html` suffix skipped; nested breadcrumb returns the immediate parent (`crumbs[-2]`).
- [x] 2.3 RED: add a pipeline-order test (mirror `test_html_title_processor.py:83`) that with `[HtmlCategoryProcessor(), HtmlToMarkdownProcessor()]` the category is captured before conversion.
- [x] 2.4 GREEN: add `_extract_kb_category(html) -> Optional[str]` (mirror `_extract_html_title`, `:94-112`) and `HtmlCategoryProcessor` (mirror `HtmlTitleProcessor`) in `processing.py`, writing via `set_metadata_field("category", ...)` only when absent and found.

## 3. Pipeline registration (TDD)

- [x] 3.1 RED: extend `test_build_persistence_factory.py` to assert the order is `HtmlTitleProcessor → HtmlCategoryProcessor → HtmlToMarkdownProcessor (→ CategorizationProcessor when enabled)` when `html_to_markdown.enabled`.
- [x] 3.2 GREEN: register `HtmlCategoryProcessor()` between `HtmlTitleProcessor()` and `HtmlToMarkdownProcessor()` in `build_persistence` (`processing.py:514-517`).

## 4. Spec sync & gate

- [x] 4.1 `openspec validate improve-fasrc-kb-ingestion --strict` passes.
- [x] 4.2 `bash scripts/gate.sh` is green: black/isort clean, full suite passes, **≥80% diff coverage** on changed lines (91.1% on `processing.py`).

## 5. Docs

- [x] 5.1 Update `docs/docs/configuration.md` (Processing section) to note the KB landmark slice (full-page fallback) and the source `category` capture (distinct from `llm_category`).

## 6. Re-ingest & deploy-verify

- [ ] 6.1 Redeploy fasrc-dev (`deploy/fasrc-dev/scripts/redeploy.sh`) so the non-editable install picks up the code change.
- [ ] 6.2 Re-ingest the KB so the 211 articles are re-persisted (body-only Markdown + `metadata["category"]`); confirm chunks refreshed under unchanged hashes (no stale/duplicate chunks).
- [ ] 6.3 Run `archi-dev-deploy-verify`: live chat HTTP-200 smoke, and spot-check that a KB chunk's `metadata` now carries a real `category` and the chunk text is article-body (no nav/sidebar/`Bookmarkable Links`).
