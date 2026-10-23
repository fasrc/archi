## Why

The hoist of a promoted code block (`_hoist_out_of_inline` in
`src/data_manager/collectors/processing.py`) has two defects that Codex found on PR #602
after its last reply round. No person answered the two threads before the merge
(issue #713; threads `r4171300227` and `r4171300228` on PR #602). Measured on
`origin/dev` `82850b3b` with `html_to_markdown`:

| Input | Output now | Defect |
|---|---|---|
| `<a href="https://x/y"><code>a<br>b</code><code>c<br>d</code><q></q></a>` | `<https://x/y>` + 2 fences + `[""](https://x/y)` | Two links to one target. |
| `<a href="https://x/y"><q></q><code>a<br>b</code></a>` | `<https://x/y>` + fence | The `<q>` is dropped and a link is synthesized. |
| `<a data-archi-link-shown href="https://x/y"><code>a<br>b</code></a>` | fence only | The link target is lost. |
| `<a href="https://x/y"><code>a<br>b</code></a>` (control) | `<https://x/y>` + fence | Correct. |

1. `_renders_link_text` counts a tag as link text only when it holds text or is in
   `_RENDERS_WITHOUT_TEXT = {"img", "hr", "video"}`. markdownify 1.2.2 writes characters
   for some tags that hold no text. An empty `<q>` gives `""`. As a result the tail half
   `<a><q></q></a>` renders a link, but the hoist reads it as "no text", does not mark
   the head half, and the head half synthesizes a second link.
2. `_LINK_SHOWN_ATTR = "data-archi-link-shown"` and `_PROMOTED_ATTR =
   "data-archi-promoted"` are internal state, but the hoist reads them from the source
   HTML too. A source anchor that carries `data-archi-link-shown` reads as "link already
   shown", so no link is synthesized and the target is lost. A source `<pre
   data-archi-promoted class="bash">` gets a `bash` fence label that a native `<pre>`
   does not get.

The issue also asks for a check of the other markdownify converters for the same shape.
The check (design D1) found that `<h1>`–`<h6>`, `<td>`, `<th>`, and `<tr>` write
characters with no text too, and give the same duplicate link today.

## What Changes

- `_RENDERS_WITHOUT_TEXT` gains `q`, `h1`–`h6`, `td`, `th`, and `tr`, the tags for which
  markdownify 1.2.2 writes characters when the tag holds no text (measured, design D1).
  An anchor split by a promoted block then renders exactly one link to its `href`.
- `_promote_block_code` removes `data-archi-link-shown` and `data-archi-promoted` from
  every tag of the source HTML right after it parses the HTML, before it promotes or
  hoists any block (design D2). The markers then come only from this module.
- The issue #430 bullet in `docs/docs/configuration.md` names the new tags and says that
  the two source attributes are ignored.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest-processing`: adds two requirements. A hoisted anchor keeps exactly one link
  when it holds a tag that writes link text with no text in it, and the hoist ignores its
  own marker attributes when they come from the source HTML.

## Impact

- Code: `src/data_manager/collectors/processing.py` (`_RENDERS_WITHOUT_TEXT`, one new
  helper, one call in `_promote_block_code`).
- Tests: `tests/unit/test_html_to_markdown_processor.py`.
- Docs: `docs/docs/configuration.md` (one bullet).
- Data: like every conversion change, the new form reaches disk only for new or
  force-overwritten documents. Neither input shape is known in the FASRC KB. All 236
  existing tests in the test file pass with the change applied (measured on `82850b3b`,
  design "Measurement").
- Out of the loop: the issue asks for a reply on both #602 threads that names the fix
  commit. That reply is a GitHub write with no commit, so it is not a task here. The
  nightly wrap-up or the reviewer does it after the PR opens.
