# Design — resolve the edge text node beside a promoted `<br>`

## Context

`html_to_markdown()` (`src/data_manager/collectors/processing.py:211`) is the single
extraction rule for the ingest and for golden-set drift detection. It calls
`_markdownify_deep_safe()` (`processing.py:397`), whose inner `_worker()`
(`processing.py:409`) runs `_promote_block_code(content)` and then `markdownify` under an
enlarged stack and a raised recursion limit (issue #40).

`_promote_block_code` (`processing.py:318`) finds every `<code>` with no `<pre>` ancestor
and at least one `<br>`. It runs two passes over the breaks: first
`_strip_break_whitespace(br)` for each (`processing.py:341-342`), then `br.replace_with("\n")`
for each (`processing.py:343-344`). The order matters and stays: a `"\n"` inserted for one
break must not be read as source whitespace of the next and stripped, which would collapse an
intended blank line.

`_strip_break_whitespace` (`processing.py:301`) looks at `br.previous_sibling` and
`br.next_sibling`. The check `type(node) is not NavigableString` (`processing.py:307`) skips
any neighbour that is a `Tag`, a `Comment`, or `None`. That is the hole: the newline is still
formatting whitespace when it sits inside an inline `<span>` next to the break, or after the
`<span>` that ends with the break.

Both files this change touches are black-clean and isort-clean on `36fdb420` (checked
2026-09-03 with black 24.10.0 and isort 6.0.1).

bs4 facts this design relies on (beautifulsoup4 4.12.3, measured 2026-09-03):

- `tag.find_all(string=True)` returns every string descendant in document order, including
  `Comment` nodes; `Comment` is a subclass of `NavigableString`, so `type(node) is
  NavigableString` is `False` for a comment and `isinstance` is `True`.
- A `<br>` element has no string descendants: `find_all(string=True)` on it returns `[]`.
- `Tag` is importable from `bs4`.

## Goals / Non-Goals

**Goals:**

- A source newline beside a `<br>` is dropped when it sits inside an inline child tag next
  to the break, or after the inline tag whose last child is the break, on either side.
- Horizontal whitespace after the dropped newline stays, so indentation survives.
- Every existing break rule stays byte-identical: the `wpautop` shape has no blank line, a
  `<br><br>` pair keeps one blank line, a tab after a dropped newline stays, and whitespace
  with no newline beside it stays.
- The search never leaves the `<code>` element being promoted.
- The two-pass structure and the deep-safe worker placement are unchanged.

**Non-Goals:**

- A `Comment` that is the direct neighbour of the `<br>` (`a<br><!-- c -->\nb`). Today
  nothing is stripped there, and this change keeps that. Measured 2026-09-03: the output
  stays `` ```\na\n\nb\n``` ``. Nothing in the corpus sample has the shape.
- An empty inline tag between the break and the newline (`a<br><span></span>\nb`). The
  first-string rule finds no text in the empty tag and stops. Output unchanged
  (`` ```\na\n\nb\n``` ``, measured 2026-09-03).
- Whitespace that is not adjacent to a `<br>`, for example a newline between two `<b>`
  children. That is the existing behaviour of `convert_pre` and is out of scope.
- `<br>` inside a native `<pre>`, the fence infostring rules, and the hoist out of inline
  formatting ancestors (#406). Those are other changes.
- Re-ingest, redeploy, or any edit to the golden-set bank.

## Decisions

### D1. Resolve an edge text node instead of requiring a direct string

A new helper `_edge_text(br, *, forward: bool, stop_at) -> NavigableString | None` returns
the text node the break touches on one side. `_strip_break_whitespace` calls it once per side
and applies the same regex to the node it returns. The alternative, a second regex pass over
the serialized `<pre>` text after promotion, was rejected: by then the `"\n"` from each `<br>`
is indistinguishable from a source newline, and the `<br><br>` blank line would be lost.

### D2. A `Tag` neighbour contributes the leaf that touches the break

`_edge_text` walks down the sibling tag along its edge child chain: the first child going
forward, the last child going backward, at each level considering only `Tag` children and
exact `NavigableString` children, so a `Comment` is looked through
(`a<br><span><!-- c -->\nb</span>` strips the newline; measured: `` ```\na\nb\n``` ``). The
walk ends at the one leaf that touches the break. A tag with no children at the edge
(`<img>`, or the `<br>` of a `<br><br>` pair) ends the walk with nothing: the text behind it
does not touch the break, so its whitespace is code payload and stays
(`a<br><span><img src="i"/>\nb</span>` keeps the newline; measured: `` ```\na\n![](i)\nb\n``` ``).
That is also what keeps a `<br><br>` pair at one blank line: neither break sees text on the
shared side.

The first version took the first or last string descendant of the whole sibling subtree
(`sibling.find_all(string=True)`), which for `<span><img src="i"/>\nb</span>` selected the
`"\nb"` behind the image and glued `b` onto it (`![](i)b`). The local adversarial review of
PR #416 on 2026-09-04 flagged the whole-subtree search; its cited example
(`<span><i>x</i>\n  b</span>`) converts identically under both rules, because `x` is the leaf
at the edge in both, but the childless-tag case above is where the rules differ.

### D3. No sibling means climb to the parent's sibling, and stop at the `<code>`

When `node.next_sibling` (or `previous_sibling`) is `None`, the helper moves `node` to its
parent and looks again, until it finds a sibling or reaches `stop_at`. Reaching `stop_at`
returns `None`: text after `</code>` is never the break's neighbour. The boundary is passed
explicitly as `stop_at=code` from `_promote_block_code`, not derived with
`br.find_parent("code")`, because a nested `<code>` inside the promoted one would otherwise
stop the climb early. The parameter is keyword-only with no default so the caller must name
the boundary.

### D4. The two regexes and the two-pass structure are unchanged

`_BR_TRAILING_WS` on the previous side and `_BR_LEADING_WS` on the next side, in that order,
then replace-or-extract, exactly as today. Issue #408 also asks to skip a string that is the
`"\n"` of another `<br>`. Under the two-pass structure that guard is unnecessary: when the
strip pass runs, every `<br>` is still a `Tag`, so no such string exists yet. The design
records this so a later reader does not add a dead guard.

### D5. The rule is deliberately shallow

The helper looks at one sibling (after any climb) and inside it at one edge string. It does
not skip past a direct `Comment` neighbour, and it does not look past an empty tag. Both are
Non-Goals above, both keep today's output, and neither appears in the corpus sample. A wider
walk is a later, measured decision.

### D6. Tests pin exact strings and stay in the existing module

All tests go in `tests/unit/test_html_to_markdown_processor.py` under a new section comment,
after the existing break-whitespace section. Tree-level tests use the module's
`_promoted_code_text` helper and assert exact text (`"a\nb"`, `"a\n    b"`). Wire tests use
`html_to_markdown()` and assert the exact fence string. One tree test asserts that the last
child of the `<p>` in `_promote_block_code('<p><code>a<br></code>\nmore</p>')` is still
`"\nmore"`, so a dropped `stop_at` cannot pass unnoticed (the Markdown output for that input
is the same either way, because block separation re-adds the newlines).

### D7. `Tag` joins the existing bs4 import line

`from bs4 import BeautifulSoup, NavigableString, Tag`. PR #414 (#406) adds the same name to
the same line; a rebase conflict there resolves to that one line.

### D8. Measured before and after (prototype on `36fdb420`, 2026-09-03)

| Input | Before | After |
|---|---|---|
| `<p><code>a<br><span>\nb</span></code></p>` | `` ```\na\n\nb\n``` `` | `` ```\na\nb\n``` `` |
| `<p><code><span>a<br></span>\nb</code></p>` | `` ```\na\n\nb\n``` `` | `` ```\na\nb\n``` `` |
| `<p><code>a<br><span>\n    b</span></code></p>` | `` ```\na\n\n    b\n``` `` | `` ```\na\n    b\n``` `` |
| `<p><code><span><em>a<br></em></span>\nb</code></p>` | `` ```\na\n\nb\n``` `` | `` ```\na\nb\n``` `` |
| `<p><code>a\n<span><br>b</span></code></p>` | `` ```\na\n\nb\n``` `` | `` ```\na\nb\n``` `` |
| `<p><code>a<br><span><!-- c -->\nb</span></code></p>` | `` ```\na\n\nb\n``` `` | `` ```\na\nb\n``` `` |
| `<p><code class="bash">#!/bin/bash<br />\n# comment<br />\necho hi</code></p>` | `` ```bash\n#!/bin/bash\n# comment\necho hi\n``` `` | unchanged |
| `<p><code>a<br><br>b</code></p>` | `` ```\na\n\nb\n``` `` | unchanged |
| `<p><code>a<br />\n<br />\nb</code></p>` | `` ```\na\n\nb\n``` `` | unchanged |
| `<p><code>all:<br>\n\tcc main.c</code></p>` | `` ```\nall:\n\tcc main.c\n``` `` | unchanged |
| `<p><code>a\tb\t<br>c</code></p>` | `` ```\na\tb\t\nc\n``` `` | unchanged |
| `<pre class="bash"><code>echo hi</code></pre>` | `` ```\necho hi\n``` `` | unchanged |
| `<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>` | `# Title\n\nAdd `--gpus=1`.` | unchanged |
| `<p><code>a<br></code>\nmore</p>` | `` ```\na\n```\n\nmore `` | unchanged |

## Risks / Trade-offs

- **A newline inside an inline child tag that the author meant as content.** Not possible in
  HTML: the element is inline, so the browser collapses it. The direct-text case already makes
  this call, and the change only extends it through inline nodes.
- **Rebase conflicts with #406, #407, #410.** All four edit `processing.py` and the same test
  module. The overlap here is the bs4 import line and the `_strip_break_whitespace` region.
  Whichever merges later rebases; the proposal says so.
- **Recursion regression through the new code.** `_edge_text` climbs with a loop, not
  recursion, and runs inside the guarded worker with the rest of the promotion. The existing
  deep-nesting tests (`-k "deeply_nested or recursion"`) must stay green.
- **Coverage.** `processing.py` is under `--cov=src`. Every new line is reached by the tests
  in D6: the direct-string branch (existing tests), the `Tag` branch with text (issue rows),
  the `Tag` branch without text (`<br><br>`), the climb (issue row two, the two-level row),
  and the `stop_at` return (the `\nmore` tree test).

## Verification (for the close-out task and the PR body)

Offline, from the branch tip in the project environment. Prints `PASS` or raises with the
first mismatch. No network and no extra package.

```bash
python - <<'EOF'
import sys; sys.path.insert(0, "")
from src.data_manager.collectors.processing import html_to_markdown
cases = {
    '<p><code>a<br><span>\nb</span></code></p>': '```\na\nb\n```',
    '<p><code><span>a<br></span>\nb</code></p>': '```\na\nb\n```',
    '<p><code>a<br><span>\n    b</span></code></p>': '```\na\n    b\n```',
    '<p><code><span><em>a<br></em></span>\nb</code></p>': '```\na\nb\n```',
    '<p><code>a\n<span><br>b</span></code></p>': '```\na\nb\n```',
    '<p><code>a<br><span><!-- c -->\nb</span></code></p>': '```\na\nb\n```',
    '<p><code class="bash">#!/bin/bash<br />\n# comment<br />\necho hi</code></p>': '```bash\n#!/bin/bash\n# comment\necho hi\n```',
    '<p><code>a<br><br>b</code></p>': '```\na\n\nb\n```',
    '<p><code>a<br />\n<br />\nb</code></p>': '```\na\n\nb\n```',
    '<p><code>all:<br>\n\tcc main.c</code></p>': '```\nall:\n\tcc main.c\n```',
    '<p><code>a\tb\t<br>c</code></p>': '```\na\tb\t\nc\n```',
    '<pre class="bash"><code>echo hi</code></pre>': '```\necho hi\n```',
    '<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>': '# Title\n\nAdd `--gpus=1`.',
}
for html, want in cases.items():
    got = html_to_markdown(html)
    assert got == want, (html, got, want)
print("PASS")
EOF
```

Baseline on `origin/dev` `36fdb420` (2026-09-03): the first six rows fail with a blank line
inside the fence; the other seven pass. After the change all thirteen pass.
