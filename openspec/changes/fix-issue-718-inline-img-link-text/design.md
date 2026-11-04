## Context

`_hoist_out_of_inline` (`src/data_manager/collectors/processing.py`) splits each inline
ancestor of a promoted block into a head half (the original tag) and a tail half (a copy).
For an `<a href>` ancestor it reads `counted = _link_text_tags(parent)` and then
`_renders_link_text(half, counted)`:

- The tail writes link text → mark the head half with `_LINK_SHOWN_ATTR`.
- Neither half writes link text and the head is not marked → replace the head half with
  a synthesized `<a href>href</a>` before the block (issue #430).

`_link_text_tags` (PR #716) reads the anchor's ancestors:

- Under `pre`, `code`, `kbd`, or `samp` (markdownify `_noformat`) → `_LINK_TEXT_TAGS_NOFORMAT`
  = `{"hr", "img", "video"}`.
- Else under `h1`–`h6`, `td`, or `th` (markdownify `_inline`) → `_LINK_TEXT_TAGS_INLINE`
  = `_RENDERS_WITHOUT_TEXT - _HEADING_TAGS` = `{"hr", "img", "q", "td", "th", "tr", "video"}`.
- Else → `_RENDERS_WITHOUT_TEXT`.

`_renders_link_text` walks `tag.descendants` and returns true at the first tag in
`counted` or the first non-blank text node. Its docstring requires the walk to stop at the
first match (Codex review on PR #602; `test_renders_link_text_stops_at_first_text`).

markdownify 1.2.2 (`markdownify/__init__.py`):

- `process_tag` adds `_inline` to the context of the children of `h1`–`h6`, `td`, `th`.
- `convert_img`: in `_inline` and with `el.parent.name` not in `keep_inline_images_in`,
  it returns `alt` only (`el.attrs.get('alt', None) or ''`).
- `convert_video`: in the same case it returns `text`, the converted children.
- `convert_a`: `chomp(text)`, then `if not text: return ''`. A blank `alt` therefore
  drops the anchor too.
- `_markdownify_deep_safe` passes only `heading_style="ATX"` and `code_language_callback`,
  so `keep_inline_images_in` is the default `[]`.

## Goals / Non-Goals

**Goals**
- In a heading, `<td>`, or `<th>`, a split anchor whose halves hold only an `<img>` without
  visible `alt` text or a `<video>` without text keeps its target as one synthesized link.
- No change of output for an `<img>` with non-blank `alt`, for an `<hr>`, for a `<video>`
  with text, or for any anchor outside a heading, `<td>`, or `<th>`.
- The 292 existing test items in `tests/unit/test_html_to_markdown_processor.py` pass with
  no change to their expected output.

**Non-Goals**
- An `<img>` or `<video>` in a split anchor under `<kbd>` or `<samp>` (also inside a cell).
  `_link_text_tags` returns the `_noformat` set first, and that set still holds `img` and
  `video`. The issue leaves this case as it is. Do not add a test that pins its output.
- Passing `keep_inline_images_in` to markdownify, or any other change of how markdownify
  writes an image or a video.
- Keeping the image in the output. In a heading or cell the image without `alt` text
  writes nothing today and after this change; only the target is recovered.
- Changing `_has_content`, the synthesized link text, or the hoist order.

## Decisions

### D1. Drop `img` and `video` from the inline set; count an `<img>` by its `alt`

```python
_LINK_TEXT_TAGS_INLINE: frozenset = (
    _RENDERS_WITHOUT_TEXT - _HEADING_TAGS - {"img", "video"}
)
```

with a two-line comment above it that names issue #718 and says why (markdownify writes
only the `alt` text of an `<img>` and only the child text of a `<video>` there).

New helper directly above `_renders_link_text`:

```python
def _img_has_alt_text(tag) -> bool:
    """True when *tag* is an ``<img>`` whose ``alt`` has visible text (issue #718).

    A heading or table cell writes only that text for the image, so it is link text
    there even though ``img`` is not in ``_LINK_TEXT_TAGS_INLINE``.
    """
    return tag.name == "img" and bool((tag.get("alt") or "").strip())
```

In `_renders_link_text`, the tag test becomes
`if node.name in counted or _img_has_alt_text(node):`.

Why this shape:

- **`<video>` needs no helper.** With `video` out of the inline set, the walk does not stop
  at the `<video>` tag and goes on into its descendants. A non-blank text child returns
  true (markdownify writes it). An `<img alt="pic">` child returns true through the helper
  (markdownify writes `pic` for it in the same `_inline` context). A `<video>` with no text
  and no such child returns false, which is what markdownify writes: nothing.
- **The helper changes the result only in the inline path.** In the other two sets `img`
  is a member, so `node.name in counted` is already true for every `<img>` and the helper
  is never the deciding test. So the output outside a heading or cell cannot change, which
  the guard test for `<p>` proves.
- **The walk stays linear.** The helper reads one attribute of the current node. No
  `get_text`, no second walk.
- `.strip()` matches markdownify's `chomp`, which also uses `str.strip()`.

### D2. Docs

In `docs/docs/configuration.md`, in the bullet "A promoted block leaves its inline
ancestors.", after the sentence that ends "where it shows no `#` (PR #716 review).", add:

> Inside a heading or table cell, an image counts only when its `alt` text is not blank,
> and a video only when it holds text, because there markdownify writes only that text
> (issue #718).

Change nothing else in the file.

## Measurement

Measured in a scratch worktree of `73104693` with the D1 code and the tests of the tasks
appendix, by `python - <<EOF` from the worktree root (`processing.__file__` printed the
scratch path, so the probe did not read the editable install).

| Input to `html_to_markdown` | Before | After |
|---|---|---|
| `<td><a href="https://x/y"><code>a<br>b</code><img src="i.png"></a></td>` | `' ``` a b ``` \|'` | `' <https://x/y>  ``` a b ``` \|'` |
| same, `alt=" "` | `' ``` a b ``` \|'` | `' <https://x/y>  ``` a b ``` \|'` |
| `<td><a href="https://x/y"><code>a<br>b</code><video src="v.mp4"></video></a></td>` | `' ``` a b ``` \|'` | `' <https://x/y>  ``` a b ``` \|'` |
| `<th><a href="https://x/y"><img src="i.png"><code>a<br>b</code></a></th>` | `' ``` a b ``` \|'` | `' <https://x/y>  ``` a b ``` \|'` |
| `<h2><a href="https://x/y"><code>a<br>b</code><img src="i.png"></a></h2>` | `'## ``` a b ```'` | `'## <https://x/y> ``` a b ```'` |
| `<table><tr><td><a href="https://x/y"><code>a<br>b</code><code>c<br>d</code><img src="i.png"></a></td></tr></table>` | `'\|  \|\n\| --- \|\n\| ``` a b ```  ``` c d ``` \|'` | `'\|  \|\n\| --- \|\n\| <https://x/y>  ``` a b ```  ``` c d ``` \|'` |
| `<td>…<img src="i.png" alt="pic"></a></td>` | `' ``` a b ```  [pic](https://x/y) \|'` | unchanged |
| `<td>…<video src="v.mp4">clip</video></a></td>` | `' ``` a b ```  [clip](https://x/y) \|'` | unchanged |
| `<td>…<video src="v.mp4"><img src="p.png" alt="pic"></video></a></td>` | `' ``` a b ```  [pic](https://x/y) \|'` | unchanged |
| `<td><a href="https://x/y"><code>a<br>b</code><hr></a></td>` | `' ``` a b ```  [---](https://x/y) \|'` | unchanged |
| `<p><a href="https://x/y"><code>a<br>b</code><img src="i.png"></a></p>` | `'```\na\nb\n```\n\n[![](i.png)](https://x/y)'` | unchanged |
| `<p><a href="https://x/y"><code>a<br>b</code><video src="v.mp4"></video></a></p>` | `'```\na\nb\n```\n\n[\\[\\](v.mp4)](https://x/y)'` (Python repr) | unchanged |
| `<td><kbd><a href="https://x/y"><code>a<br>b</code><img src="i.png"></a></kbd></td>` (non-goal) | `' ``` a b ``` \|'` | unchanged |

Test counts for `python -m pytest -q tests/unit/test_html_to_markdown_processor.py`:

- Before the change: 292 test items collected.
- Tests of the appendix added, D1 code NOT applied (red): **6 failed, 298 passed**. The 6
  are the 4 items of `test_hoist_textless_img_or_video_in_cell_keeps_target`,
  `test_hoist_textless_img_in_heading_keeps_target`, and
  `test_hoist_textless_img_in_table_after_two_blocks_keeps_target`. Every guard passes.
- Tests and D1 code (green): **304 passed**.
- The 11 unit test files that import `collectors.processing` or `html_to_markdown`: **552
  passed** with the change.

## Risks / Trade-offs

- A page that has an `<img>` without `alt` in a linked heading or table cell next to a
  multi-line `<code>` now gets one more `<https://...>` link in its Markdown. This is the
  fix: the target was lost before.
- No other caller reads `_LINK_TEXT_TAGS_INLINE` or `_renders_link_text`
  (`grep -rn` over `src/` and `tests/`: the hoist's two calls and
  `test_renders_link_text_stops_at_first_text`).
