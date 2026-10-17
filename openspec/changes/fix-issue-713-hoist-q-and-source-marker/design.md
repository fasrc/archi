## Context

`_promote_block_code` (`src/data_manager/collectors/processing.py`) parses the page HTML
with `BeautifulSoup(html, "html.parser")`, wraps each multi-line `<code>` that is not
under a `<pre>` in a new `<pre data-archi-promoted="">`, and then calls
`_hoist_out_of_inline` for each new block, last block first. It is the only parse before
markdownify: `html_to_markdown` → `_markdownify_deep_safe` →
`_markdownify(_promote_block_code(content), ...)`. No other path reaches markdownify.

`_hoist_out_of_inline` splits each inline ancestor of a block into a head half (the
original tag) and a tail half (a copy). For an `<a href>` ancestor it uses
`_renders_link_text(half)` to decide whether a half will render a link:

- The tail renders link text → mark the head half with `_LINK_SHOWN_ATTR`, so a later
  (earlier-in-source) block that empties the head does not add a second link.
- Neither half renders link text and the head is not marked → replace the head with a
  synthesized link `<a href>href</a>` before the block (issue #430).

`_renders_link_text` walks descendants and returns true at the first non-blank text
node or the first tag in `_RENDERS_WITHOUT_TEXT = {"img", "hr", "video"}`.
`_promoted_fence_language` labels a `<pre>` only when it has `_PROMOTED_ATTR`.

## Goals / Non-Goals

**Goals**
- An anchor that the hoist splits renders exactly one link to its `href` when it holds
  a tag that markdownify writes as characters with no text in the tag.
- `data-archi-link-shown` and `data-archi-promoted` in the source HTML have no effect.
- No change to the output for any anchor that holds none of the newly counted tags and
  no source marker attribute. The 236 existing tests in
  `tests/unit/test_html_to_markdown_processor.py` pass unchanged.

**Non-Goals**
- Removing other `data-archi-*` attributes. No other name with that prefix exists in
  `src/` (`grep -rn "data-archi" src/` finds only the two constants).
- Changing how markdownify renders a `<q>`, a heading, or a table cell inside a link
  (`[""](...)`, `[#](...)`, ` [|](...)` are the library's own output).
- Changing `_has_content`, the synthesized link text, or the hoist order.
- Replying on the #602 threads (a GitHub write with no commit; the wrap-up or the
  reviewer does it after the PR opens).

## Decisions

### D1. Add `q`, `h1`–`h6`, `td`, `th`, `tr` to `_RENDERS_WITHOUT_TEXT`

The issue asks for `<q>` and for a check of the other converters for the same shape.
The check: for each tag that `_ArchiMarkdownConverter` has a `convert_<tag>` method for
(plus `h1`–`h6`, `span`, `source`, `input`, `wbr`), convert
`<p><a href="https://x/y"><TAG></TAG></a></p>` (a void tag without the close tag) with
`_markdownify(..., heading_style="ATX")` and look for the `href` in the output. A tag
with no converter renders only its children, so it cannot write characters with no text.
Result on markdownify 1.2.2:

| Tag | Output | Why |
|---|---|---|
| `q` | `[""](https://x/y)` | `convert_q` returns `'"' + text + '"'` |
| `h1`–`h6` | `[#](https://x/y)` … `[######](https://x/y)` | `convert_hN` writes the ATX hashes |
| `td`, `th` | `[\|](https://x/y)` | the cell converters write `' ' + text + ' \|'` |
| `tr` | `[\|\n\|  \|](https://x/y)` | `convert_tr` writes the row and an underline row |
| `hr`, `img` | already in the set | unchanged |

No other tag gives a link. `video` stays in the set (unchanged; it renders a link when
it has a `src`). Each new tag gives the same duplicate link as `<q>` today:
`<a href="https://x/y"><code>a<br>b</code><code>c<br>d</code><h1></h1></a>` gives
`<https://x/y>` + 2 fences + `[#](https://x/y)` on `82850b3b`.

The new set (keep the name, keep it a `frozenset`, one name per entry, sorted is fine):
`{"h1", "h2", "h3", "h4", "h5", "h6", "hr", "img", "q", "td", "th", "tr", "video"}`.
Change the comment above it to say what the set now holds: the tags that make markdownify
write link text even when they hold no text. Add issue #713 to the `_renders_link_text`
docstring.

A non-empty tag already holds text, so `_renders_link_text` already returns true for it.
Only the empty case changes.

**Reading of the issue constraint.** The issue says "Do not change the output for any
input that has no empty `<q>` and no `data-archi-*` attribute". Its plan step 3 asks to
add every converter with the same shape. These two conflict only for an empty heading or
table cell inside a split anchor, and each of those inputs gives a duplicate link today
(the same defect). This design obeys plan step 3 and holds the constraint for every
other input. The measured proof is the "Measurement" section: no existing test changes.

### D2. Drop the source markers in `_promote_block_code`

Add a helper next to `_promote_block_code`:

```python
# The attributes that only this module may set (issue #713).
_HOIST_MARKER_ATTRS: tuple = (_PROMOTED_ATTR, _LINK_SHOWN_ATTR)


def _drop_source_markers(soup) -> None:
    """Remove the hoist's marker attributes from every tag of the source HTML.

    ``_promoted_fence_language`` and ``_hoist_out_of_inline`` read the markers as
    their own state, so a marker in the scraped HTML would label a native ``<pre>``
    or hide a link target (issue #713).
    """
    for tag in soup.find_all(True):
        for name in _HOIST_MARKER_ATTRS:
            tag.attrs.pop(name, None)
```

Call it in `_promote_block_code` directly after `soup = BeautifulSoup(html,
"html.parser")` and before the `for code in soup.find_all("code")` loop. Add one
sentence to the `_promote_block_code` docstring. The helper is one pass over the tags,
so it is linear in the page size, like the existing `find_all("code")`.

`html.parser` lowercases attribute names, so `DATA-ARCHI-LINK-SHOWN` is removed too.
A promoted block still gets `_PROMOTED_ATTR`, because the helper runs before the
promotion loop sets it.

### D3. Docs

In `docs/docs/configuration.md`, the bullet "A promoted block leaves its inline
ancestors." has the sentence "The exception is a link left with no text, image, rule, or
video on any side of the fences (issue #430)". Change "no text, image, rule, or video" to
"no text, image, rule, video, quotation, heading, or table cell" and add "(issue #713)"
after "(issue #430)". At the end of the same bullet's link sentences (after "becomes
`[/docs](/docs)`."), add: "The conversion ignores a `data-archi-link-shown` or
`data-archi-promoted` attribute in the page HTML (issue #713): these names are its own
markers." Do not change any other text in the file.

### D4. Count the new tags only where the link shows its href (PR #716 review)

markdownify's `convert_a` writes only the link text, never the `href`, under a `pre`,
`code`, `kbd`, or `samp` ancestor (its `_noformat` context), and `convert_hN` writes no
hashes under a heading, `td`, or `th` ancestor (its `_inline` context). Counting a D1 tag
in those places hides the synthesized target and gains nothing: on the D1-only code,
`<kbd><a href="https://x/y"><code>a<br>b</code><q></q></a></kbd>` gives the fence and
`` `""` `` with no `https://x/y`, and `<td><a href="https://x/y"><code>a<br>b</code><h1></h1></a></td>`
gives the cell with no `https://x/y`. Before D1 both kept the target.

`_link_text_tags(anchor)` reads the anchor's ancestors and returns the counted set: only
`img`, `hr`, `video` under a no-format ancestor, all D1 tags except the headings under a
heading or cell, and the full set elsewhere. The hoist splits only inline markup, so both
halves keep the ancestors it reads. `img`, `hr`, and `video` keep their old behavior in
every context (the Goals ask for no change to anchors without a D1 tag).

## Measurement

Prototype on `82850b3b`: the D1 set and the D2 strip applied at run time (no file
edit). Results:

- All 236 tests in `tests/unit/test_html_to_markdown_processor.py` pass, the same count
  as without the prototype. No existing expected output changes.
- The first probe input of the issue gives exactly one `https://x/y` link.
- Exact outputs of every scenario in `specs/ingest-processing/spec.md` were read from
  the prototype.

## Risks / Trade-offs

- An empty heading or cell inside a link now renders as `[#](href)` or ` [|](href)`
  instead of a synthesized `<href>` where markdownify writes those characters. Both keep
  the target once. The text is markdownify's own rendering of the tag. Elsewhere D4
  keeps the synthesized target.
- One more pass over all tags per converted page. The conversion already walks the tree
  several times; the cost is linear.
