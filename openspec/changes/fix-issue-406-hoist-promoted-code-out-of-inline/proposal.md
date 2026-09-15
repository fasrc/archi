# Hoist a promoted code block out of inline formatting ancestors in the HTML-to-Markdown ingest

## Why

PR #405 (issue #399) added `_promote_block_code(html)` to
`src/data_manager/collectors/processing.py:318`. For each `<code>` element with no `<pre>`
ancestor and at least one `<br>`, it drops the source newlines beside each break, replaces
each `<br>` with a newline, and wraps the element **in place** in a new `<pre>` that carries
`_PROMOTED_ATTR`. `markdownify` 1.2.2 then emits the fence from `convert_pre`.

The wrap is in place. When the `<code>` sits under an inline tag that `markdownify` renders
with markup, the new `<pre>` stays inside that tag, and the tag's converter wraps the whole
fence in its markers. Measured on `origin/dev` `36fdb420` (2026-09-03) through
`html_to_markdown()`:

| Input | Output today | Why it is wrong |
|---|---|---|
| `<p><em><code>a<br># heading</code></em></p>` | `'*```\na\n# heading\n```*'` | `*```` is a paragraph line, `# heading` is an ATX heading, and the closing ```` ```* ```` opens a new fence |
| `<p><strong><code class="bash">a<br># heading</code></strong></p>` | `'**```bash\na\n# heading\n```**'` | same, and the `bash` infostring is lost |
| `<p><a href="http://x">See <code>a<br>b</code> now</a></p>` | `'[See\n\n```\na\nb\n```\n\nnow](http://x)'` | the link text spans a block; CommonMark does not parse it as a link |
| `<p><kbd><code>a<br># heading</code></kbd></p>` | `` '```` ```\na\n# heading\n``` ````' `` | the fence sits inside a four-backtick inline span, so `# heading` is a heading |

`markdown-it-py` 4.2.0 in `commonmark` mode parses the first two outputs to one heading
each. Before #405 the same inputs produced an inline span with hard breaks
(`'*`a  \n# heading`*'`), which is the #399 defect itself. #405 did not regress these
shapes, and it did not fix them.

The tags `markdownify` 1.2.2 renders with markup are `a`, `b`, `strong`, `em`, `i`, `del`,
`s`, `kbd`, `samp`, `sub`, `sup` (read from the installed module on 2026-09-03:
`abstract_inline_conversion` for eight of them, `convert_a` for the link, and
`convert_kbd = convert_samp = convert_code`). Every other inline tag passes its text
through, so a `<pre>` inside it converts correctly today.

The shape is rare in the corpus. A 60-page sample of the 213 KB pages in
`https://docs.rc.fas.harvard.edu/sitemap.xml` (seed 399, fetched 2026-09-02) held 25
multi-line bare `<code>` elements, and 0 of them sat under a marked inline ancestor. The fix
is defensive. Codex raised the finding on PR #405
(`https://github.com/fasrc/archi/pull/405#discussion_r3911513054`); the nightly review
responder answered it with these numbers and filed #406 instead of widening that diff.

## What Changes

- A new module-level frozenset `_INLINE_MARKUP_TAGS` in
  `src/data_manager/collectors/processing.py` with exactly the eleven tag names above.
- A new module-level helper `_hoist_out_of_inline(pre, soup) -> None`, called once in
  `_promote_block_code` directly after `code.wrap(pre)` (`processing.py:349`). While the
  parent of the promoted `<pre>` is a tag in `_INLINE_MARKUP_TAGS`, it splits that parent
  around the block: the siblings after the `<pre>` move into a new tag with the same name
  and attributes, the `<pre>` moves after the parent, the new tag is inserted after the
  `<pre>` when it has content, and the parent is removed when it has no content left. The
  whitespace of each half that touches the cut is dropped.
- New tests in `tests/unit/test_html_to_markdown_processor.py`: tree assertions on
  `_promote_block_code` and exact-string assertions through `html_to_markdown()` for the
  four inputs in issue #406, the nested case, the sibling-text split, and the existing
  guards.

No new dependency. No file outside those two changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest-processing`: one requirement is ADDED. The #399 change
  (`openspec/changes/fix-issue-399-fence-multiline-code`) is merged but not archived, so its
  requirement "Multi-line bare code converts to a fenced block" is not in
  `openspec/specs/ingest-processing/spec.md` yet. This change adds its own requirement
  rather than modifying one that the main spec does not hold.

## Impact

- `src/data_manager/collectors/processing.py` — one frozenset, one helper with a small
  edge-whitespace helper, one call-site line.
- `tests/unit/test_html_to_markdown_processor.py` — new tests, no existing test changed.
- Extracted text changes only for pages where a multi-line bare `<code>` sits under a marked
  inline tag: 0 of 25 such elements in the 60-page sample, so the corpus effect today is
  zero or near zero. Sibling text around the block is emitted as two marked runs
  (`**Note:**` … `**done**`) instead of one run around the whole block.
- `html_to_markdown()` is the golden-set drift-hash source
  (`src/utils/goldenset_maintenance.py`), so the digest moves only for affected pages. The
  #405 PR body recorded the bank state: 105 draft rows, no digest, no lock.
- **No re-ingest and no redeploy** in this change. Persisted pages update only on a force
  re-ingest, which is a separate operator decision.
- #407, #408, and #410 edit the same function or the same `_worker()` call and the same test
  module. Whichever merges later rebases onto the earlier one. None of them touches the
  hoist loop.
