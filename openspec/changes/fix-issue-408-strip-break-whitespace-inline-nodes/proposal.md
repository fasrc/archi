# Strip break whitespace through inline nodes beside a promoted `<br>` in the HTML-to-Markdown ingest

## Why

PR #405 (issue #399) added `_promote_block_code(html)` to
`src/data_manager/collectors/processing.py:318`. For each bare multi-line `<code>` element it
calls `_strip_break_whitespace(br)` (`processing.py:301`) for every `<br>` in a first pass,
then replaces each `<br>` with `"\n"` in a second pass (`processing.py:341-344`).
`_strip_break_whitespace` reads `br.previous_sibling` and `br.next_sibling` and acts only when
the neighbour is a direct text node of exact type `NavigableString` (`processing.py:307`). It
removes `(?:[ \t]*\r?\n[ \t]*)+$` from the text before the break and `^(?:[ \t]*\r?\n)+` from
the text after it (`_BR_TRAILING_WS` and `_BR_LEADING_WS`, `processing.py:290-291`).

When the neighbour is a `Tag`, or when the `<br>` is the last child of an inline tag and the
newline sits after that tag, the source newline survives the promotion and becomes a blank
line inside the fence. Measured on `origin/dev` `36fdb420` (2026-09-03) through
`html_to_markdown()`:

| Input | Output today | Expected |
|---|---|---|
| `<p><code>a<br><span>\nb</span></code></p>` | `'```\na\n\nb\n```'` | `'```\na\nb\n```'` |
| `<p><code><span>a<br></span>\nb</code></p>` | `'```\na\n\nb\n```'` | `'```\na\nb\n```'` |
| `<p><code>a<br><span>\n    b</span></code></p>` | `'```\na\n\n    b\n```'` | `'```\na\n    b\n```'` |

The source element is inline. A browser collapses every newline in it, so each of those
newlines is formatting whitespace, not content. The fence must not turn one into a blank line.
The third row shows that the horizontal whitespace after the dropped newline must stay, so an
indented code line keeps its indentation, the same rule the direct-text case already obeys.

Corpus evidence from issue #408 (measured 2026-09-02 on a 60-page sample of the 213 KB pages
in `https://docs.rc.fas.harvard.edu/sitemap.xml`, seed 399): 25 multi-line bare `<code>`
elements, 0 with any child tag other than `<br>`, and 0 of their 107 breaks with a `Tag` as a
neighbour. WordPress emits `<br />\n` as direct text siblings, which the current code handles
(106 of 107 breaks in the sample). The fix is defensive: it closes a hole the current corpus
does not exercise, so that a page with a different authoring tool does not gain blank lines
inside every fence. Codex raised the finding on PR #405:
<https://github.com/fasrc/archi/pull/405#discussion_r3912257994>.

The defect is fork-local. `processing.py` and `markdownify` are absent from `upstream/dev`.
Nothing is filed upstream.

## What Changes

- A new module-level helper `_edge_text(br, *, forward, stop_at)` in
  `src/data_manager/collectors/processing.py`, placed directly above
  `_strip_break_whitespace`. It returns the text node a `<br>` touches on one side, looking
  through inline tags: a direct `NavigableString` neighbour is returned as is; a `Tag`
  neighbour yields its first (next side) or last (previous side) string descendant of exact
  type `NavigableString`, so a `Comment` is skipped; when the `<br>` has no sibling on that
  side, the search climbs to the parent's sibling and stops at the `<code>` element that is
  being promoted. It returns `None` when there is no such text node.
- `_strip_break_whitespace(br, *, stop_at)` gains a keyword-only `stop_at` parameter and
  resolves each neighbour through `_edge_text` instead of reading `previous_sibling` and
  `next_sibling` directly. The two regexes, the replace-or-extract step, and the two-pass
  structure in `_promote_block_code` are unchanged.
- The one call in `_promote_block_code` becomes `_strip_break_whitespace(br, stop_at=code)`.
- `Tag` is added to the existing `from bs4 import BeautifulSoup, NavigableString` line.
- New tests in `tests/unit/test_html_to_markdown_processor.py`: the three inputs above as
  exact fence strings through `html_to_markdown()`, tree-level tests through the existing
  `_promoted_code_text` helper for the next side, the previous side, a two-level climb, and a
  `Comment` inside the inline tag, plus a tree test that the climb never passes the `<code>`
  element. Every existing test in the module stays as it is.

No new dependency. No file outside those two and this change's `openspec/` files.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest-processing`: one requirement is ADDED. The requirements that PR #405 wrote for the
  promotion live in the unarchived change `openspec/changes/fix-issue-399-fence-multiline-code/`
  and are not yet in `openspec/specs/ingest-processing/spec.md`, so this delta adds its own
  requirement rather than modifying one the base spec does not hold.

## Impact

- `src/data_manager/collectors/processing.py` — one new helper, one changed helper
  signature and body, one changed call, one import.
- `tests/unit/test_html_to_markdown_processor.py` — new tests, no existing test changed.
- Extracted text changes only for a page whose bare multi-line `<code>` carries a source
  newline inside or beside an inline child tag next to a `<br>`. In the 2026-09-02 sample no
  break has that shape, so no live KB page is expected to change and the golden-set drift
  digest (`html_to_markdown()` is its source) is expected to stay put for every sampled page.
- **No re-ingest and no redeploy** in this change. A conversion fix reaches disk only for
  newly ingested pages or a force-overwrite; the persistence layer skips a path that exists.
- #406 (PR #414), #407 (PR #415), and #410 edit the same function and the same test module.
  PR #414 also adds `Tag` to the same import line. Whichever merges later rebases onto the
  earlier one; the overlap is one import line and the region around
  `_strip_break_whitespace`.
