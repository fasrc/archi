## 1. Count an image by its alt text and a video by its text in a heading or cell (red tests and fix in one task)

- [ ] 1.1 In `tests/unit/test_html_to_markdown_processor.py`, insert the test block of
      Appendix A2 VERBATIM (copy it; do not rewrite it). Insert it right AFTER the end of
      `test_hoist_kbd_inside_link_does_not_stop_the_count` and BEFORE the comment line
      `# --- hoist marker attributes in source HTML are ignored (issue #713) ----------`.
      Do NOT append at the end of the file. The 7 test names are new (checked against the
      file on `73104693`); grep each name once more before you insert it, because a
      duplicate `def` silently deletes the older test. Run
      `python -m pytest -q tests/unit/test_html_to_markdown_processor.py` and confirm
      EXACTLY `6 failed, 298 passed` (the 6 are listed in Appendix A4; every guard
      passes). Then, in the same task, make the three code edits of Appendix A1 in
      `src/data_manager/collectors/processing.py` (design D1). Re-run the test file and
      confirm EXACTLY `304 passed`. If any count differs, or any test that existed before
      changes its expected output, STOP and write what you saw under "## Measurement" in
      `design.md`. Run `bash scripts/gate.sh` and commit only when it is green (message:
      `count an inline img by its alt text and a video by its text`). Read the trailing
      context line of the test-file diff before you commit: no existing `assert` line may
      sit below the inserted block.

## 2. Docs

- [ ] 2.1 In `docs/docs/configuration.md`, in the bullet "A promoted block leaves its
      inline ancestors.", insert the sentence of Appendix A3 right after the text
      ``where it shows no `#` (PR #716 review).`` and before `A relative or fragment
      target`. Keep the bullet's line wrapping at about 88 characters and change nothing
      else in the file. Run `bash scripts/gate.sh` and commit when green (message:
      `document the inline img and video link text rule`).

## 3. Publish

- [ ] 3.1 Tick this box, commit the tick through the hook (message: `mark #718 tasks
      complete`), then push and open the PR. Push:
      `git push -u origin fix/issue-718-inline-img-link-text`. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-718-inline-img-link-text`
      with the title `fix(ingest): keep the link target for a textless img or video in a
      heading or table cell` and `Closes #718` in the BODY (not the title). Pass the body
      with `--body` or a file under `/tmp`; do NOT commit a PR-body file to the
      repository. In the body: link the first use of "knowledge base" to
      `https://github.com/fasrc/archi/blob/dev/docs/docs/glossary.md#kb-knowledge-base`,
      copy the "## Measurement" table of `design.md`, state that no existing test changed
      its expected output, and add one line: "Non-goal (issue #718): an `<img>` or
      `<video>` in a split anchor under `<kbd>` or `<samp>` is unchanged." STOP at the
      open PR — do not merge.

## Appendix (reference for task 1.1 and 2.1; not tasks)

### A1. Code edits in `src/data_manager/collectors/processing.py`

Measured in a scratch worktree of `73104693`; this is the exact diff after `black`.

```diff
@@ _INLINE_CONTEXT_TAGS: frozenset = _HEADING_TAGS | {"td", "th"}
 # The issue #713 tags count only where they make the anchor show its href, so they
 # do not hide the synthesized target anywhere else (Codex review on PR #716).
 _LINK_TEXT_TAGS_NOFORMAT: frozenset = frozenset({"hr", "img", "video"})
-_LINK_TEXT_TAGS_INLINE: frozenset = _RENDERS_WITHOUT_TEXT - _HEADING_TAGS
+# In a heading or table cell markdownify writes only the alt text of an <img> and only
+# the child text of a <video>, so neither tag counts there by name (issue #718).
+_LINK_TEXT_TAGS_INLINE: frozenset = (
+    _RENDERS_WITHOUT_TEXT - _HEADING_TAGS - {"img", "video"}
+)
@@ def _link_text_tags(anchor) -> frozenset:
     return _RENDERS_WITHOUT_TEXT
 
 
+def _img_has_alt_text(tag) -> bool:
+    """True when *tag* is an ``<img>`` whose ``alt`` has visible text (issue #718).
+
+    A heading or table cell writes only that text for the image, so it is link text
+    there even though ``img`` is not in ``_LINK_TEXT_TAGS_INLINE``.
+    """
+    return tag.name == "img" and bool((tag.get("alt") or "").strip())
+
+
 def _renders_link_text(tag, counted: frozenset = _RENDERS_WITHOUT_TEXT) -> bool:
@@ def _renders_link_text(tag, counted: frozenset = _RENDERS_WITHOUT_TEXT) -> bool:
     for node in tag.descendants:
         if isinstance(node, Tag):
-            if node.name in counted:
+            if node.name in counted or _img_has_alt_text(node):
                 return True
         elif type(node) is NavigableString and node.strip():
             return True
```

### A2. Test block for `tests/unit/test_html_to_markdown_processor.py`

Insert as written (it is already `black`-clean). `_A` is the module constant
`"```\na\nb\n```"` defined near line 975. Keep two blank lines before the block and two
blank lines after it.

````python
# --- an image without alt text or a video writes nothing in a cell (issue #718) ---


@pytest.mark.parametrize(
    "html",
    [
        '<td><a href="https://x/y"><code>a<br>b</code><img src="i.png"></a></td>',
        '<td><a href="https://x/y"><code>a<br>b</code><img src="i.png" alt=" "></a></td>',
        '<td><a href="https://x/y"><code>a<br>b</code><video src="v.mp4"></video></a></td>',
        '<th><a href="https://x/y"><img src="i.png"><code>a<br>b</code></a></th>',
    ],
)
def test_hoist_textless_img_or_video_in_cell_keeps_target(html):
    """In a table cell markdownify writes only the alt text of an <img> and only the
    child text of a <video>, so a split link with neither keeps its target (#718)."""
    assert html_to_markdown(html) == " <https://x/y>  ``` a b ``` |"


def test_hoist_textless_img_in_heading_keeps_target():
    """A heading is the same context: an <img> without alt text writes nothing."""
    assert (
        html_to_markdown(
            '<h2><a href="https://x/y"><code>a<br>b</code><img src="i.png"></a></h2>'
        )
        == "## <https://x/y> ``` a b ```"
    )


def test_hoist_textless_img_in_table_after_two_blocks_keeps_target():
    """Two blocks and an <img> without alt text in a table keep one target (#718)."""
    assert (
        html_to_markdown(
            '<table><tr><td><a href="https://x/y"><code>a<br>b</code>'
            '<code>c<br>d</code><img src="i.png"></a></td></tr></table>'
        )
        == "|  |\n| --- |\n| <https://x/y>  ``` a b ```  ``` c d ``` |"
    )


def test_hoist_img_with_alt_in_cell_is_link_text():
    """Guard: an <img> with alt text writes that text in a cell; output unchanged."""
    assert (
        html_to_markdown(
            '<td><a href="https://x/y"><code>a<br>b</code>'
            '<img src="i.png" alt="pic"></a></td>'
        )
        == " ``` a b ```  [pic](https://x/y) |"
    )


@pytest.mark.parametrize(
    "inner, text",
    [("clip", "clip"), ('<img src="p.png" alt="pic">', "pic")],
)
def test_hoist_video_with_text_in_cell_is_link_text(inner, text):
    """Guard: a <video> writes its child text in a cell, an inner image's alt text
    too; output unchanged (issue #718)."""
    assert (
        html_to_markdown(
            f'<td><a href="https://x/y"><code>a<br>b</code>'
            f'<video src="v.mp4">{inner}</video></a></td>'
        )
        == f" ``` a b ```  [{text}](https://x/y) |"
    )


def test_hoist_rule_in_cell_is_link_text():
    """Guard: an <hr> still counts in a cell; output unchanged (issue #718)."""
    assert (
        html_to_markdown('<td><a href="https://x/y"><code>a<br>b</code><hr></a></td>')
        == " ``` a b ```  [---](https://x/y) |"
    )


@pytest.mark.parametrize(
    "tail, link_text",
    [
        ('<img src="i.png">', "![](i.png)"),
        ('<video src="v.mp4"></video>', "\\[\\](v.mp4)"),
    ],
)
def test_hoist_textless_img_or_video_outside_cell_is_link_text(tail, link_text):
    """Guard: outside a heading or cell markdownify writes the image or video as a
    link, so it is link text there; output unchanged (issue #718)."""
    assert (
        html_to_markdown(f'<p><a href="https://x/y"><code>a<br>b</code>{tail}</a></p>')
        == _A + f"\n\n[{link_text}](https://x/y)"
    )
````

### A3. Docs sentence for `docs/docs/configuration.md`

> Inside a heading or table cell, an image counts only when its `alt` text is not blank,
> and a video only when it holds text, because there markdownify writes only that text
> (issue #718).

### A4. Expected test counts (`python -m pytest -q tests/unit/test_html_to_markdown_processor.py`)

- On `73104693` before task 1.1: 292 test items collected.
- A2 inserted, A1 not applied: `6 failed, 298 passed`. The 6 failures:
  - `test_hoist_textless_img_or_video_in_cell_keeps_target` (all 4 parameters)
  - `test_hoist_textless_img_in_heading_keeps_target`
  - `test_hoist_textless_img_in_table_after_two_blocks_keeps_target`
- A2 and A1: `304 passed`.
