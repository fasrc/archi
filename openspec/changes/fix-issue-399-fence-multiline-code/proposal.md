# Fence multi-line bare `<code>` in the HTML-to-Markdown ingest

## Why

`src/data_manager/collectors/processing.py:298` is the only conversion call in the ingest:
`markdownify(content, heading_style="ATX")`. It is reached from `html_to_markdown()`
(`processing.py:211`) through `_markdownify_deep_safe()` (`processing.py:284`). The pins are
`markdownify==1.2.2` and `beautifulsoup4==4.12.3` (`requirements/requirements-base.txt:4-5`,
`pyproject.toml:54,60`).

`markdownify` 1.2.2 converts a `<code>` element that has no `<pre>` ancestor to a
single-backtick span, and converts each `<br>` inside it to a two-space hard break. Measured
on 2026-09-02 against the pinned version:

    >>> markdownify('<p><code class="bash">#!/bin/bash<br># comment<br>echo hi</code></p>', heading_style='ATX')
    '`#!/bin/bash  \n# comment  \necho hi`'

CommonMark parses block structure before inline structure, and an inline code span cannot
cross a block boundary. Every line inside that span that starts with `# ` at column 0 leaves
the span and becomes a real ATX heading. The code block is destroyed and spurious headings
take its place.

The FASRC knowledge base serves this shape. `https://docs.rc.fas.harvard.edu/kb/helmod-faq`
carries 0 `<pre>` elements and 103 `<code>` elements (re-counted 2026-09-02). Of those, 13
are multi-line bare `<code>` elements with a language class (`bash` x8, `lua` x3, `spec` x2).
The other 90 are single-line and carry no class. Run through the project's own
`html_to_markdown()` today, that page yields 40 headings, of which 11 are shell comment
lines, and 0 fenced blocks. Issue #399 measured 38 flattened blocks across 14 of the 213
ingested KB pages, and 14 false headings across 3 pages.

The harm is active today: the persisted text is what the chat shows a user and what the
RAGAS golden set scores against. It is also latent: `MarkdownNodeParser` at
`src/data_manager/vectorstore/node_parsing.py:231` splits by heading, so under the supported
`markdown` chunking strategy those false headings become chunk boundaries. The live
deployment runs `chunking.strategy: sentence`, so that path is not active right now.

The defect is fork-local. `processing.py` and `markdownify` are absent from `upstream/dev`.
Nothing is filed upstream.

## What Changes

- A new module-level helper `_promote_block_code(html: str) -> str` in
  `src/data_manager/collectors/processing.py`. It parses the HTML with BeautifulSoup. For
  every `<code>` element that has no `<pre>` ancestor and contains at least one `<br>`, it
  replaces each `<br>` with a newline and wraps the element in a new `<pre>` that carries the
  `<code>` element's `class` attribute. It returns the re-serialized HTML. Every other
  element is untouched.
- A new module-level callback `_fence_language(pre) -> str` and a frozenset
  `_FENCE_LANGUAGES`. The callback returns the first class on the `<pre>` element that is in
  the allowlist, compared lowercase, or `""` when none is. The allowlist is `bash`, `sh`,
  `spec`, `lua`, `python`, `c`, `cpp`, `fortran`, `r`, `perl`, `json`, `yaml`, `text`.
- The one `markdownify(...)` call inside `_worker()` in `_markdownify_deep_safe` becomes
  `markdownify(_promote_block_code(content), heading_style="ATX", code_language_callback=_fence_language)`.
  The normalization runs inside the worker thread, under the enlarged stack and the raised
  recursion limit, as issue #399 requires.
- New tests in `tests/unit/test_html_to_markdown_processor.py` for the helper, the callback,
  and the end-to-end conversion: fenced output with an infostring, a bare fence for a
  non-allowlisted class, inline code left inline, an existing `<pre><code>` block
  byte-identical, the deep-nesting guard still green, and the processor persisting the same
  text the seam returns.

No new dependency. No file outside those two changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest-processing`: two requirements are ADDED. The existing requirement "HTML-to-Markdown
  conversion at persist time" (`openspec/specs/ingest-processing/spec.md:6`) promises that
  headings, lists, links, and tables survive. It says nothing about code, so its text stays
  as it is and the new behaviour gets its own requirements.

## Impact

- `src/data_manager/collectors/processing.py` — helper, callback, allowlist, one call-site
  line.
- `tests/unit/test_html_to_markdown_processor.py` — new tests, no existing test changed.
- **Every page's extracted text can change**, not only pages with multi-line code. The
  BeautifulSoup round trip (`str(soup)`) recovers content that the current path drops. Issue
  #399's follow-up comment measured a pure no-op round trip on 12 live KB pages: 8 of 12
  changed, +944 characters net, never negative. On `/kb/helmod-faq` today the round trip
  alone adds 198 characters (15400 to 15598) and the fences add 99 more. This is an
  improvement, not a regression, and the PR reports it separately from the code-fence
  numbers.
- `html_to_markdown()` is the golden-set drift-hash source
  (`src/utils/goldenset_maintenance.py:1336`), so the digest moves for every page whose text
  changes. Issue #399's comment checked the bank: all 105 rows are `status: draft` and none
  carries a digest or lock. The bank lives outside this repository, so the loop cannot
  re-check it. The PR states this.
- **No re-ingest and no redeploy** in this change. Re-ingest is a separate operator decision
  because it re-scrapes the corpus and would break any golden-set campaign pinned to the
  current corpus fingerprint.
- #400 (empty `<h2></h2>` headings) touches the same function. Whichever merges second
  builds on the first one's BeautifulSoup pass rather than adding a second parse.
