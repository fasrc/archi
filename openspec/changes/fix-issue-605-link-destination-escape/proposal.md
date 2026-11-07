## Why

`html_to_markdown` writes every link as `[text](href)` with the `href` as it is
(`markdownify` 1.2.2 `MarkdownConverter.convert_a`). CommonMark ends a bare link
destination at a space or at an unmatched `)`, so some links come back with a wrong
target (issue #605). Measured on `origin/dev` `0ed50a23`:

- `<p><a href="/docs/foo)bar">Docs</a></p>` → `[Docs](/docs/foo)bar)` (target `/docs/foo`)
- `<p><a href="https://x/a b">Docs</a></p>` → `[Docs](https://x/a b)` (not a link)
- `<p><a href="/docs/foo)bar"><code>a<br>b</code></a></p>` →
  `[/docs/foo)bar](/docs/foo)bar)` + fence (the link that the hoist keeps, issue #430)
- `<p><a href="https://x/a b">https://x/a b</a></p>` → `<https://x/a b>` (an autolink
  cannot hold a space, so this is plain text)

The knowledge base then stores a wrong or broken target for these links.

## What Changes

- `_ArchiMarkdownConverter.convert_a` in `src/data_manager/collectors/processing.py`
  writes the destination in angle brackets (`[Docs](</docs/foo)bar>)`) when the bare
  form is not valid: the `href` has a space or other whitespace, or its parentheses are
  not balanced. Inside the angle brackets, `<` and `>` get a backslash.
- The same rule applies to the kept link of issue #430 (`[href](href)`).
- An autolink (`<href>`) that cannot hold its `href` (whitespace, `<`, or `>`) becomes an
  explicit link `[href](<href>)`.
- A link whose `href` has no whitespace and only balanced parentheses converts
  byte-for-byte as before. This is the usual case, so most of the corpus does not change.
- One bullet in `docs/docs/configuration.md` next to the other conversion items.

## Capabilities

### New Capabilities

### Modified Capabilities

- `ingest-processing`: a new requirement that link destinations round-trip through
  CommonMark.

## Impact

- Code: `src/data_manager/collectors/processing.py` (`convert_a` and one small helper).
- Tests: `tests/unit/test_html_to_markdown_processor.py`, next to the link tests.
- Docs: `docs/docs/configuration.md`.
- Corpus: the change reaches disk only for new or force-overwritten documents.
- Overlap: open PR #664 (issue #604) also edits `convert_a`. The PR that merges second
  must rebase on the first.
