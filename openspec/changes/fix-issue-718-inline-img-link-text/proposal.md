## Why

The hoist of a promoted code block (`_hoist_out_of_inline` in
`src/data_manager/collectors/processing.py`) splits an `<a href>` around the block. When
neither half writes link text, it replaces the anchor with a synthesized `<a href>href</a>`,
so the target stays in the knowledge base (issue #430). It decides with
`_renders_link_text(half, counted)`, where `counted = _link_text_tags(anchor)` (PR #716).

In a heading, `<td>`, or `<th>` (markdownify's `_inline` context), `_link_text_tags` returns
`_LINK_TEXT_TAGS_INLINE`, which still holds `img` and `video`. But markdownify 1.2.2 writes
only the `alt` text of an `<img>` there (`convert_img`), and only the child text of a
`<video>` (`convert_video`). The project passes no `keep_inline_images_in`. So an `<img>`
without visible `alt` text, or a `<video>` without text, writes nothing. `convert_a` gets
empty text and drops the anchor, and the hoist synthesizes no link because it counted the
tag as link text. The target is lost (issue #718; Codex thread on PR #716,
`r4301659281`).

Measured on `origin/dev` `73104693` (2026-11-04), the same as the issue's table on
`82850b3b`:

| Input to `html_to_markdown` | Output | Target kept? |
|---|---|---|
| `<td><a href="https://x/y"><code>a<br>b</code><img src="i.png"></a></td>` | `' ``` a b ``` \|'` | no |
| `<td><a href="https://x/y"><code>a<br>b</code><img src="i.png" alt=" "></a></td>` | `' ``` a b ``` \|'` | no |
| `<td><a href="https://x/y"><code>a<br>b</code><video src="v.mp4"></video></a></td>` | `' ``` a b ``` \|'` | no |
| `<th><a href="https://x/y"><img src="i.png"><code>a<br>b</code></a></th>` | `' ``` a b ``` \|'` | no |
| `<h2><a href="https://x/y"><code>a<br>b</code><img src="i.png"></a></h2>` | `'## ``` a b ```'` | no |
| `<td><a href="https://x/y"><code>a<br>b</code><img src="i.png" alt="pic"></a></td>` | `' ``` a b ```  [pic](https://x/y) \|'` | yes |
| `<td><a href="https://x/y"><code>a<br>b</code></a></td>` | `' <https://x/y>  ``` a b ``` \|'` | yes |

## What Changes

- `_LINK_TEXT_TAGS_INLINE` drops `img` and `video` (design D1). A `<video>` in a heading
  or cell then counts only through the text it holds, which `_renders_link_text` already
  visits as descendants.
- A new helper `_img_has_alt_text(tag)` returns true for an `<img>` whose `alt` holds
  non-whitespace text. `_renders_link_text` counts a tag when `node.name in counted` or
  `_img_has_alt_text(node)`. The walk stays linear and still stops at the first match.
- The bullet "A promoted block leaves its inline ancestors." in
  `docs/docs/configuration.md` gets one sentence: inside a heading or table cell an image
  counts only when its `alt` text is not blank, and a video only when it holds text.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest-processing`: adds one requirement. In a heading or table cell, a split anchor
  counts an `<img>` as link text only when its `alt` holds text, and a `<video>` only
  through its text. It uses `## ADDED Requirements` because the #713 requirement is not
  archived into `openspec/specs/` yet.

## Impact

- Code: `src/data_manager/collectors/processing.py` (one constant, one new helper, one
  condition in `_renders_link_text`).
- Tests: `tests/unit/test_html_to_markdown_processor.py`, 7 new test functions (12 test
  items), next to the PR #716 tests.
- Docs: `docs/docs/configuration.md` (one sentence in one bullet).
- Data: like every conversion change, the new form reaches disk only for new or
  force-overwritten documents.
- Unchanged by design: an `<img>` with non-blank `alt`, an `<hr>`, a `<video>` with text,
  and every anchor outside a heading, `<td>`, or `<th>`. All 292 existing test items in
  the test file pass with no change to their expected output (design "Measurement").
