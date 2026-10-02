# Keep the link when the hoist empties an anchor (#430)

## Why

The #406 hoist splits an inline ancestor around a promoted code block and drops a half that
has no content. When an anchor holds only the code block, both halves are empty, so
`parent.decompose()` (`src/data_manager/collectors/processing.py:438`) deletes the anchor
and its `href`. Measured on `origin/dev` `570814d0`:

- `<p><a href="https://x/y"><code>a<br>b</code></a></p>` converts to `` ```\na\nb\n``` ``.
- The titled form (`title="Docs"`) and the nested `<a href><em><code>` form give the same
  string. The link target is lost from the knowledge base.

The operator chose option B on 2026-09-26 (issue #430 body): keep the link.

## What Changes

- In `_hoist_out_of_inline` (`processing.py:418`), when the parent is an `a` with a
  non-empty `href` and both halves are empty after the split, replace the empty anchor with
  a new `<a href="...">` whose text is the anchor's stripped `title`, else the `href`. The
  new link sits in the anchor's place, which is directly before the `<pre>`.
- An anchor with other content beside the code block keeps today's behaviour: the half
  with content carries the `href`, so no extra link is added.
- An anchor with no `href`, or an empty `href`, is dropped as today.
- Add unit tests in `tests/unit/test_html_to_markdown_processor.py`.
- Amend the unarchived #406 delta
  (`openspec/changes/fix-issue-406-hoist-promoted-code-out-of-inline/specs/ingest-processing/spec.md:15`)
  so its "drop a half" sentence names this exception.

## Out of scope

- Other inline parents (`em`, `strong`, `span`): they carry no `href`, so a drop loses
  nothing.
- How `markdownify` renders a link whose text equals a relative `href` (it emits
  `</docs>`, which is not a CommonMark autolink). This is the library's behaviour for every
  such anchor today; see design D3.
- Archiving the #406 change.

## Impact

- Code: `src/data_manager/collectors/processing.py` (`_hoist_out_of_inline` only).
- Tests: `tests/unit/test_html_to_markdown_processor.py`.
- Corpus: the #406 review found zero pages with this shape, so no corpus digest moves.
