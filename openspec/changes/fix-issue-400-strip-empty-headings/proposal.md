## Why

The HTML→Markdown conversion turns an empty heading into a bare ATX marker: `<h3></h3>`
becomes `### ` in the ingested Markdown (issue #400). The dev corpus has 15 such headings
across 213 pages. A bare marker is noise in a chunk and an empty section to a header-aware
splitter. The operator chose on 2026-09-26 to strip them unconditionally (option A).

## What Changes

- The pure function `html_to_markdown` drops every `h1`–`h6` element whose stripped text is
  empty and that has no `img` or `code` descendant, before it converts the page.
- The removal runs in the BeautifulSoup pass that already exists in `_promote_block_code`,
  so the page is parsed once.
- No configuration key. The ingest path (`HtmlToMarkdownProcessor`) and the golden-set drift
  check (`goldenset_maintenance.extract_page_text`) both call `html_to_markdown`, so the two
  stay identical with no plumbing.
- `docs/docs/configuration.md` says that empty headings are dropped.

## Capabilities

### New Capabilities

### Modified Capabilities

- `ingest-processing`: conversion drops empty headings.

## Impact

- Code: `src/data_manager/collectors/processing.py` only.
- Tests: `tests/unit/test_html_to_markdown_processor.py`.
- Docs: `docs/docs/configuration.md`.
- Data: about 12 page digests in the golden-set drift check change on the next run. No bank
  row is locked on them, so no bank edit is needed (the bank is in the config repo).
