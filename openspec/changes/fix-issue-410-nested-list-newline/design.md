# Design — put the newline back after a nested list

## Context

`html_to_markdown()` (`src/data_manager/collectors/processing.py:211`) is a pure function
and the single extraction rule for the ingest and for golden-set drift detection. It calls
`_markdownify_deep_safe()` (`processing.py:397`), whose inner `_worker()` runs
`markdownify(_promote_block_code(content), heading_style="ATX", code_language_callback=_promoted_fence_language)`
(`processing.py:411`) inside a worker thread with a 64 MiB stack and a 16,000 recursion
limit, serialized by `_CONVERSION_LOCK`. Anchors are at `origin/dev` `dc6772a2`, 2026-09-04.

Three facts about `markdownify` 1.2.2 shape this design (read with `inspect.getsource`,
2026-09-04):

1. `convert_list(el, text, parent_tags)` returns `'\n' + text.rstrip()` when
   `'li' in parent_tags`, and `'\n\n' + text + ('\n' if before_paragraph else '')`
   otherwise. The nested branch strips the trailing newline and never checks what follows.
2. `convert_ul = convert_list` and `convert_ol = convert_list` are class attributes bound at
   class-definition time. `get_conv_fn` looks up `convert_<tag>`, so for a `<ul>` it calls
   `convert_ul`, which is the base `convert_list` object. A subclass that overrides only
   `convert_list` changes nothing: the first prototype of this design did exactly that and
   every one of 21 shapes came out byte-identical.
3. `convert_li` does `text.strip()` on its content and returns `'%s\n' % text` with no
   leading newline. So a trailing newline added by a nested list that ends its item is
   removed by the item, and a sibling `<li>` that follows a nested list (the malformed shape)
   starts on the same line.

Which followers already start on a new line was measured, not assumed. For each converter
name on the pinned class, `<ul><li>Outer<ul><li>Inner</li></ul><X>c</X></li></ul>` was
converted and the text after `Inner` inspected:

| Follower output starts on a new line | Follower is glued to `Inner` |
|---|---|
| `article`, `blockquote`, `div`, `dl`, `dt`, `figcaption`, `h1`–`h6`, `hr`, `ol`, `p`, `pre`, `section`, `table`; `br` emits `'  \n'` so the line ends | a non-blank text node; `a`, `code`, `img`, `span`; `li`, `dd`, `caption`, `q`; every tag with no converter (`figure`, `nav`, `details`, `summary`, `small`, `font`, `video`, `iframe`, `label`, `abbr`, `time`, `cite`, ...) |

`should_remove_whitespace_outside`, the library's own "is block" test, is the wrong
predicate: it counts `li`, `dd`, `td`, and `th` as block elements although their converters
emit no leading newline, so the malformed sibling item (issue Snippet D) would stay glued.

## Goals / Non-Goals

**Goals:**

- Content that follows a nested list inside the same list item starts on its own line, so a
  closing code fence is never joined to it.
- Prose, inline elements, and a sibling item get the same break. Ordered lists too.
- Every shape that is correct today converts byte-identically: a nested list that ends its
  item, a following paragraph, code block, heading, list, or `<br>`, an empty nested list,
  and a top-level list followed by anything.
- One seam: `html_to_markdown()` keeps calling the same code path, and the two existing
  tests that monkeypatch `processing.markdownify` pass unchanged.

**Non-Goals:**

- The fence tolerance in `src/data_manager/vectorstore/node_parsing.py` (`_closes_fence`).
  It stays as the safety net for the persisted corpus.
- A `markdownify` bump.
- `<pre>` inside `<dd>` or table cells flattened onto one line. The issue names it as a
  different defect in the same family.
- Fence delimiter sizing (#407, PR #415), promoted-block hoisting (#406, PR #414), and
  break whitespace through inline nodes (#408, PR #416).
- Re-ingest, redeploy, or any edit to the golden-set bank.
- Filing the repro upstream. The loop's token cannot open issues on another repository; the
  PR body carries the repro for a human to file.

## Decisions

### D1. Fix the join in the converter, not in the HTML normalization

The alternative in the issue, extending `_promote_block_code` to wrap post-list content in a
`<p>`, was rejected. A sibling `<li>` (Snippet D) cannot be wrapped in a `<p>` without
changing the list structure, and a `<p>` wrap emits `\n\n`, a blank line, where the library
dropped exactly one newline. The converter override inserts one newline at the one place it
was removed and touches nothing else.

### D2. Rebind `convert_ul` and `convert_ol` in the subclass

```python
class _ArchiMarkdownConverter(MarkdownConverter):
    def convert_list(self, el, text, parent_tags):
        out = super().convert_list(el, text, parent_tags)
        if "li" in parent_tags and _nested_list_needs_break(el, text):
            return out + "\n"
        return out

    convert_ul = convert_list
    convert_ol = convert_list
```

The two rebinds are the whole reason the override is reached (Context, fact 2). A direct
test pins them: `_ArchiMarkdownConverter.convert_ul is _ArchiMarkdownConverter.convert_list`
and the same for `convert_ol`, so a later refactor that drops one line fails a named test
instead of silently converting like the base class.

### D3. The predicate: a real follower whose Markdown does not start on a new line

```python
_SELF_SEPARATING_FOLLOWERS = frozenset({
    "article", "blockquote", "br", "div", "dl", "dt", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "ol", "p", "pre", "section", "table",
})

def _next_content_sibling(el):
    sib = el.next_sibling
    while sib is not None:
        if isinstance(sib, Tag):
            return sib
        if isinstance(sib, NavigableString) and not isinstance(sib, (Comment, Doctype)):
            if str(sib).strip():
                return sib
        sib = sib.next_sibling
    return None

def _nested_list_needs_break(el, text) -> bool:
    if not text.strip():
        return False
    nxt = _next_content_sibling(el)
    if nxt is None:
        return False
    return not (isinstance(nxt, Tag) and nxt.name in _SELF_SEPARATING_FOLLOWERS)
```

The set is the measured left column of the Context table. It is an allowlist of followers
that need no help, so the failure direction is safe: a self-separating element missing from
the set yields one extra blank line (harmless Markdown), while an element wrongly in the set
would leave the glue in place. `script` and `style` emit nothing and are left out on
purpose; a newline before an empty string costs nothing. Comments and doctypes are skipped
because they are not content: `<!-- c -->tail` is glued today (`Innertail`) and must be
separated, while a comment as the only follower must add nothing.

### D4. An empty nested list adds nothing

`<ul><li>Outer<ul></ul>tail</li></ul>` converts to `'* Outer\n  tail'` today: there is no
last line to glue onto. The `text.strip()` guard keeps that output byte-identical.

### D5. The module keeps a function named `markdownify`

```python
from markdownify import MarkdownConverter

def markdownify(html: str, **options) -> str:
    """Mirror ``markdownify.markdownify`` with the project converter (issue #410)."""
    return _ArchiMarkdownConverter(**options).convert(html)
```

The library's own wrapper is the same one-liner over the base class. Keeping the name means
the `_worker()` call site is not edited, and `test_converter_raises_keeps_original` and
`test_blank_output_keeps_original`, which patch
`"src.data_manager.collectors.processing.markdownify"`, pass unchanged, as the issue
requires. The class, the helpers, the frozenset, and the wrapper are placed directly above
`_markdownify_deep_safe`, after `_promoted_fence_language`.

### D6. Deep pages

`super().convert_list` adds one Python frame per nested-list level. The conversion already
runs inside the deep-safe worker (64 MiB stack, 16,000-frame limit), so the existing
deep-nesting tests (`-k "deeply_nested or recursion"`) cover the new path and must stay green.
The prototype hit `RecursionError` on a 300-deep synthetic list only when run outside that
worker under the default 1,000-frame limit, and the base class failed the same way.

### D7. Tests pin byte-identity for the untouched shapes

As in #399's design, guards assert the exact string the conversion produces today, not a
"contains" check, so the override cannot drift them unnoticed:

| Shape | Today's output, must stay exact |
|---|---|
| `<ul><li>Outer<ul><li>Inner</li></ul></li><li>Next outer</li></ul>` | `'* Outer\n  + Inner\n* Next outer'` |
| `<ul><li>Outer<ul><li>Inner</li></ul><p>Para</p></li></ul>` | `'* Outer\n  + Inner\n\n  Para'` |
| `<ul><li>Outer<ul><li>Inner</li></ul><pre>code</pre></li></ul>` | ``'* Outer\n  + Inner\n\n  ```\n  code\n  ```'`` |
| `<ul><li>Outer<ul><li>Inner</li></ul><h3>Head</h3></li></ul>` | `'* Outer\n  + Inner\n\n  ### Head'` |
| `<ul><li>Outer<ul><li>Inner</li></ul><ul><li>Second</li></ul></li></ul>` | `'* Outer\n  + Inner\n  + Second'` |
| `<ul><li>Outer<ul><li>Inner</li></ul><br>tail</li></ul>` | `'* Outer\n  + Inner  \n  tail'` |
| `<ul><li>Outer<ul></ul>tail</li></ul>` | `'* Outer\n  tail'` |
| `<ul><li>Outer<ul><li>Inner</li></ul><!-- c --></li></ul>` | `'* Outer\n  + Inner'` |
| `<ul><li>a</li></ul>tail text` | `'* a\n\ntail text'` |

And the repaired shapes, measured with the prototype on 2026-09-04:

| Shape | Before | After |
|---|---|---|
| Snippet A (fence) | ``'* Outer item\n  + Inner:\n\n    ```\n    x = 1\n    ```After the nested list.'`` | ``'* Outer item\n  + Inner:\n\n    ```\n    x = 1\n    ```\n  After the nested list.'`` |
| Snippet C (prose) | `'* Outer item\n  + Inner ends in proseAfter the nested list.'` | `'* Outer item\n  + Inner ends in prose\n  After the nested list.'` |
| Snippet D (sibling item) | ``'* A\n  + ```\n    docker rm alpine\n    ```* Configure a bundle'`` | ``'* A\n  + ```\n    docker rm alpine\n    ```\n  * Configure a bundle'`` |
| `<ol><li>Outer<ol><li>Inner</li></ol>After.</li></ol>` | `'1. Outer\n   1. InnerAfter.'` | `'1. Outer\n   1. Inner\n   After.'` |
| `<ul><li>Outer<ul><li>Inner</li></ul><a href="http://x">link</a> tail</li></ul>` | `'* Outer\n  + Inner[link](http://x) tail'` | `'* Outer\n  + Inner\n  [link](http://x) tail'` |
| `<ul><li>Outer<ul><li>Inner</li></ul><!-- c -->tail</li></ul>` | `'* Outer\n  + Innertail'` | `'* Outer\n  + Inner\n  tail'` |

### D8. Tests are appended under a banner and the file's last line stays intact

New tests go at the end of `tests/unit/test_html_to_markdown_processor.py` under a comment
banner that names issue #410. Nightly runs have inserted new tests above the file's final
line and silently swallowed the previous test's last assertion, so the last existing line
(`assert _promoted_code_text("<p><code>a\tb\t<br>c</code></p>") == "a\tb\t\nc"`) must be
unchanged in the diff's trailing context.

### D9. The docs bullet

One bullet in `docs/docs/configuration.md`, after "Multi-line code becomes a fenced block."
and before "Cost.", in the same voice as its neighbours: content after a nested list starts
on its own line; a following paragraph, code block, heading, or list is unchanged; like the
body slice, the change reaches disk only for new or force-overwritten documents.

## Interplay with open PRs

Three open PRs edit the same two files (checked 2026-09-04):

- **PR #415 (#407)** adds `_ArchiMarkdownConverter` with a `convert_pre` override, a
  `_markdownify(html, **options)` wrapper, imports `STRIP, STRIP_ONE, MarkdownConverter,
  strip1_pre, strip_pre`, and changes the two monkeypatch targets to `processing._markdownify`.
  Its class docstring already says issue #410 adds `convert_list` alongside `convert_pre`.
  This change uses the same class name so the merge is a union, not a rename. Rules for
  whichever lands second: one class with both overrides; one wrapper; the `_worker()` call
  and the two test patch targets use the wrapper name that is on `dev`. If #415 is on `dev`
  when this branch is rebased, drop this change's class header and wrapper hunks, add
  `convert_list`, the two rebinds, the helpers, and the frozenset to the existing class, and
  leave the tests' patch targets as `dev` has them.
- **PR #414 (#406)** and **PR #416 (#408)** edit `_promote_block_code` and
  `_strip_break_whitespace` and add tests at the end of the test file. No overlap with the
  converter class. The test-file tail can conflict textually; resolve by keeping both blocks.

## Risks / Trade-offs

- **A follower that emits a leading newline but is not in the set** gets one extra blank
  line. Accepted: harmless Markdown, and the set was measured over every converter on the
  pinned class. A `markdownify` bump would need the table re-measured; the pin is exact.
- **A `<br>` right after a nested list** keeps today's hard break (`Inner  \n  tail`), so a
  closing fence followed by `<br>` reads ```` ```  ```` with two trailing spaces. CommonMark
  allows trailing spaces on a closing fence. Not a defect; not changed.
- **Extracted text changes on every page with a repaired join.** Measured on the live page:
  +18 characters, text identical once whitespace is removed. The golden-set digest moves for
  those pages only. The bank is outside this repository; the PR says so.
- **Pages already on disk keep the glue** until a force re-ingest. Documented in the PR and
  in the docs bullet; the chunker's tolerance covers them meanwhile.

## Live verification (for the close-out task and the PR body)

Run from the branch tip in the project environment. Network access to
`slurm.schedmd.com` is required; if it is unavailable, do not fail the task and use the
2026-09-04 numbers below in the PR body.

```bash
curl -sL --max-time 30 https://slurm.schedmd.com/containers.html -o /tmp/containers.html
python - <<'EOF'
import re
from src.data_manager.collectors.processing import html_to_markdown
md = html_to_markdown(open("/tmp/containers.html", encoding="utf-8").read())
open_run, glued = None, 0
for i, line in enumerate(md.split("\n"), 1):
    m = re.match(r"^[ \t]*(?:>[ \t]?)*[ \t]*(`{3,}|~{3,})(.*)$", line)
    if not m:
        continue
    run, rest = m.group(1), m.group(2)
    if open_run is None:
        open_run = run
    elif run[0] == open_run[0] and len(run) >= len(open_run):
        if rest.strip():
            glued += 1
            print(i, repr(line.strip()[:70]))
        open_run = None
print("glued closers on the live page:", glued)
print("chars:", len(md))
EOF
```

Baseline measured 2026-09-04 with the pinned converter on the fetched page: 3 glued closers
(lines 573, 775, 920), 36,948 characters. Prototype of this design on the same HTML: 0 glued
closers, 36,966 characters (+18), and the text is identical once whitespace is removed.
