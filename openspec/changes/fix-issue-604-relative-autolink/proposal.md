# Render relative and fragment self-links as Markdown links (#604)

## Why

`markdownify` 1.2.2 `convert_a` writes `<href>` when the link text equals the `href`. It
does not check for a URI scheme, but a CommonMark autolink needs an absolute URI
(`scheme:...`). Measured on `origin/dev` `6156b760` with `html_to_markdown`:

- `<p><a href="/docs">/docs</a></p>` → `'</docs>'` (CommonMark reads a raw HTML end tag)
- `<p><a href="#sec">#sec</a></p>` → `'<#sec>'` (not a link)
- `<p>see<a href="/docs"> /docs </a>now</p>` → `'see</docs>now'` (the autolink shortcut
  also drops the whitespace around the link)
- `<p><a href="https://x/y">https://x/y</a></p>` → `'<https://x/y>'` (correct)

Persisted HTML keeps relative anchors (`scraper.py` resolves them only for its crawl list),
so this shape reaches the knowledge base. PR #602 (issue #430) fixed the shape only for the
link that the hoist keeps (`_KEPT_LINK_ATTR`, `processing.py:302`, and the marker condition
in `_ArchiMarkdownConverter.convert_a`, `processing.py:695`). The operator note on #604
(2026-10-02) says: widen that override to all self-links, drop the marker condition, and do
not add a second override.

## What Changes

- `_ArchiMarkdownConverter.convert_a` (`src/data_manager/collectors/processing.py`): when
  `markdownify` returns the autolink shortcut `<href>` and the `href` has no URI scheme,
  return `prefix[text](href)suffix` instead, for every anchor (design D1, D2).
- Do not rewrite inside `_noformat` contexts (`code`, `kbd`, `samp`, `pre`), where
  `convert_a` returns the plain text and that text can look like `<href>` (design D3).
- Remove the `_KEPT_LINK_ATTR` marker: its constant, its comment, and its use in
  `_hoist_out_of_inline`. Nothing else reads it.
- Update the class and method docstrings.
- Add unit tests in `tests/unit/test_html_to_markdown_processor.py`.
- Add one bullet to the conversion list in `docs/docs/configuration.md`.

## Out of scope

- Link destinations with a space or an unbalanced parenthesis, and escaping `[`/`]` in link
  text: issue #605 (same method; it rebases on this change).
- `href` loss under `kbd`, `samp`, or `code` (markdownify `_noformat`): a separate cause, no
  issue yet.
- Absolute URLs (`https:`, `http:`, `mailto:`): they stay `<...>` autolinks, byte-for-byte.
- Re-converting the existing corpus: the change reaches disk only for new or
  force-overwritten documents.

## Impact

- Code: `src/data_manager/collectors/processing.py` (one method, one constant, one call).
- Tests: `tests/unit/test_html_to_markdown_processor.py`.
- Docs: `docs/docs/configuration.md`.
- No config, dependency, deploy, or CI change.
