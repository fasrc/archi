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

- [x] 6.1 Redeploy fasrc-dev (`deploy/fasrc-dev/scripts/redeploy.sh`) so the non-editable install picks up the code change. Deployed branch code (`archi source commit 26a4dbb0`); all containers healthy, no crash-loop / `ModuleNotFoundError`.
- [x] 6.2 Re-ingest the KB (body-only Markdown + `metadata["category"]`). NOTE: a plain re-ingest does **not** refresh existing chunks (content-only change under an unchanged `.md` filename/hash — see the ingest-processing "Re-ingest refreshes chunks" caveat), so this required `nuke.sh` + fresh ingest. Result: 366 docs / 2970 chunks; corpus-wide leak check = **0** chunks containing `Bookmarkable Links` or `Filter by categories`.
- [x] 6.3 `archi-dev-deploy-verify`: chat smoke = **HTTP 200** with a real grounded answer (model `local/palmfuture/Qwen3.6-35B`); **446** KB chunks carry a `category` spanning all **19** taxonomy terms; `llm_category` now has real labels (0 `uncategorized`, fixing the pre-existing state). Non-KB sources (slurm/wiki) correctly have `category = NULL`.
