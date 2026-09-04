# Design — split the inline ancestor around a promoted code block

## Context

`_promote_block_code` (`src/data_manager/collectors/processing.py:318`) runs inside
`_worker()` of `_markdownify_deep_safe` (`processing.py:397`), under the enlarged stack and
the raised recursion limit that issue #40 requires. It parses the page with
`BeautifulSoup(html, "html.parser")`. For each bare multi-line `<code>` it strips the
whitespace beside each `<br>`, replaces each `<br>` with `"\n"`, and wraps the element in a
new `<pre>` that carries `_PROMOTED_ATTR` (`processing.py:345-349`). `str(soup)` then goes
to `markdownify(..., heading_style="ATX", code_language_callback=_promoted_fence_language)`.

How `markdownify` 1.2.2 renders the ancestors (read from the installed module on
2026-09-03):

- `b`, `strong`, `em`, `i`, `del`, `s`, `sub`, `sup` use `abstract_inline_conversion`:
  `chomp(text)` lifts one leading and one trailing space outside the markers, then the rest
  is wrapped in the marker pair.
- `a` uses `convert_a`: `chomp`, then `[text](href "title")`.
- `kbd` and `samp` are aliases of `convert_code`: the text is wrapped in a backtick run one
  longer than the longest run inside it.
- `q` wraps in `"` … `"` with no `chomp`. Measured:
  `<p><q><code>a<br># heading</code></q></p>` converts to
  `'"\n\n```\na\n# heading\n```\n\n"'`. The fence is valid and `markdown-it-py` finds no
  heading, so `q` stays out of scope.
- `span`, `u`, `mark`, `small`, `abbr`, `cite`, `var`, `dfn`, `font`, `label` have no
  converter and pass their text through.
- `process_tag` drops a whitespace-only text node next to a block element
  (`should_remove_whitespace_outside` includes `pre`), and `process_text` strips text next
  to a block. Both look at direct siblings only, so whitespace inside an inline tag that sits
  next to the block survives.

Both files this change touches are black-clean and isort-clean on `36fdb420` (checked
2026-09-03).

## Goals / Non-Goals

**Goals:**

- A promoted block whose `<code>` sits under one or more tags from the marked set converts to
  a fence that opens and closes on lines of its own, with no marker on the fence lines.
- Sibling text inside the split ancestor keeps its formatting on both sides of the block.
- An `<a>` keeps its `href` and `title` on both halves.
- No change to inline single-line `<code>`, a native `<pre>`, the wpautop shape, or the
  deep-nesting guard.

**Non-Goals:**

- A transparent inline tag between the `<code>` and a marked ancestor
  (`<em><span><code>…</code></span></em>`). The loop stops at the `<span>`, so that shape
  still converts to `'*```\na\nb\n```*'` (measured). The 60-page sample (2026-09-02) held one
  `<span>`-wrapped element, and it had no marked ancestor above it. A later, measured change
  can widen the loop.
- `<q>`: see Context. The fence stays valid.
- The fence delimiter length (#407), whitespace inside inline children of the `<code>`
  (#408), and the nested-list newline (#410).
- Re-ingest, redeploy, and the golden-set bank.

## Decisions

### D1. Split the ancestor; never `unwrap()` it

`parent.unwrap()` would drop the formatting of the sibling text:
`<strong>Note: <code>a<br>b</code></strong>` would turn `Note:` from bold to plain. The split
keeps the formatting on both sides: `<em>X<pre/>Y</em>` becomes `<em>X</em><pre/><em>Y</em>`.
This is the decision issue #406 records, and the prototype confirms it (D8).

### D2. The marked set is exactly the eleven tags the converter renders with markup

```python
_INLINE_MARKUP_TAGS = frozenset(
    {"a", "b", "strong", "em", "i", "del", "s", "kbd", "samp", "sub", "sup"}
)
```

This is the set from issue #406, re-read from the pinned module (Context). It is a module
constant so a test can pin it and a future `markdownify` bump can be checked against it.

### D3. The loop condition is the parent's name

```python
while isinstance(pre.parent, Tag) and pre.parent.name in _INLINE_MARKUP_TAGS:
```

The `BeautifulSoup` object itself is a `Tag` named `[document]`, so a promoted block at the
top level stops the loop. Nested ancestors (`<a><em><code>…`) are split one level per
iteration, innermost first, and the `<pre>` climbs until its parent is outside the set.
Measured: `'[*See*](http://x)\n\n```\na\nb\n```\n\n[*now*](http://x)'`.

### D4. The split, step by step

1. `parent = pre.parent`.
2. `tail = soup.new_tag(parent.name, attrs=dict(parent.attrs))` — a new tag with the same
   name and a copy of every attribute (`href`, `title`, `class`, …). `class` is a list under
   bs4's multi-valued attributes and serializes back to `class="x y"` (measured).
3. `for node in list(pre.next_siblings): tail.append(node)` — the list is taken first,
   because `append` extracts each node from the parent as it moves.
4. `parent.insert_after(pre)` — bs4 extracts the `<pre>` from the parent and places it as the
   parent's next sibling. The parent now holds only the head content.
5. Drop the whitespace at the cut on both halves (D6).
6. `if _has_content(tail): pre.insert_after(tail)`.
7. `if not _has_content(parent): parent.decompose()`.

The head half is the original tag object, so it keeps its attributes without a copy. The
`soup` is passed in because `new_tag` lives on it, and `_promote_block_code` already holds
it.

### D5. A half has content when it holds a tag or non-blank text

`_has_content(tag)` is true when any direct child is a `Tag`, or is an exact
`NavigableString` (not a `Comment` or another subclass) with non-whitespace text. So
`<a href="u"><img src="i.png"></a>` is kept (measured:
`'[![](i.png)](http://x)\n\n```\na\nb\n```'`), `<em> </em>` is dropped, and
`<em><!-- c --></em>` is dropped because a comment renders nothing.

### D6. Whitespace at the cut is dropped

After the split, the head half often ends in a space and the tail half often starts with
one (`<strong>Note: </strong>` / `<strong> done</strong>`). `chomp` lifts each space outside
the markers, and `process_tag` collapses only newlines, so the output would be
`'**Note:** \n\n```\na\nb\n```\n\n **done**'`: a trailing space on the line before the fence
and a leading space on the line after it. Both are noise at a block boundary, and the
converter already removes the same whitespace when the text is a direct sibling of the
block. So the helper walks down each half along its edge child (the last child of the head
half, the first child of the tail half) to the one text node that touches the cut,
right-strips it on the head half or left-strips it on the tail half, and extracts a node that
becomes empty. Comments are looked through because they are not exact `NavigableString`s. A
tag with no children at the edge (`<img>`) ends the walk with nothing to trim: the text
behind it does not touch the cut (Codex review on PR #414 caught the earlier form, which
trimmed the last text node of the whole half and so dropped the space in
`<em>x <img src="i.png"/><code>…`). Measured result:
`'**Note:**\n\n```\na\nb\n```\n\n**done**'`. Whitespace that does not touch the cut is kept:
`<em>x <img src="i.png"/> <code>a<br>b</code></em>` keeps the space after `x` (measured:
`'*x ![](i.png)*\n\n```\na\nb\n```'`).

### D7. The call sits inside `_promote_block_code`, after `code.wrap(pre)`

One line, `_hoist_out_of_inline(pre, soup)`, directly after `processing.py:349`. The helper
runs inside `_worker()` for free, so the #40 guard covers it. It is a `while` loop, not
recursion. The promotion loop collects every new `<pre>` and a second pass hoists them
last-to-first: with the later blocks already out of the ancestor, the tail half of each
split holds only the nodes up to the next block, so every sibling moves once. Hoisting in
document order, as the first version did, moved the whole remaining tail once per block —
quadratic in the block count (Codex review on PR #414; measured on one `<em>` holding 2000
multi-line `<code>` siblings: 10.6 s of hoist time before, 0.45 s after). The final tree is
the same in either order, because each split partitions the ancestor's children the same
way. Measured: `'*x*\n\n```\na\nb\n```\n\n*y*\n\n```\nc\nd\n```\n\n*z*'`.

### D8. Tests pin exact strings and the tree

All tests go in `tests/unit/test_html_to_markdown_processor.py`. Tree tests call
`_promote_block_code` and assert on the parsed result: the parent name of the `<pre>`, the
names, text, and attributes of the halves. End-to-end tests call `html_to_markdown()` and
assert the exact string for each of the four issue inputs plus the nested and split cases,
so a converter change cannot drift the output unnoticed. A small test helper checks the
structural properties the issue asks for: exactly two lines start with ```` ``` ```` at
column 0, the closing one is exactly ```` ``` ````, and no line starts with `# `.
`markdown-it-py` is not imported in tests.

Prototype results (2026-09-03, `36fdb420`, pinned `markdownify` 1.2.2 and `beautifulsoup4`
4.12.3):

| Input | Before | After |
|---|---|---|
| `<p><em><code>a<br># heading</code></em></p>` | `'*```\na\n# heading\n```*'` | `'```\na\n# heading\n```'` |
| `<p><strong><code class="bash">a<br># heading</code></strong></p>` | `'**```bash\na\n# heading\n```**'` | `'```bash\na\n# heading\n```'` |
| `<p><a href="http://x">See <code>a<br>b</code> now</a></p>` | `'[See\n\n```\na\nb\n```\n\nnow](http://x)'` | `'[See](http://x)\n\n```\na\nb\n```\n\n[now](http://x)'` |
| `<p><strong>Note: <code>a<br>b</code> done</strong></p>` | `'**Note:\n\n```\na\nb\n```\n\ndone**'` | `'**Note:**\n\n```\na\nb\n```\n\n**done**'` |
| `<p><a href="http://x"><em>See <code>a<br>b</code> now</em></a></p>` | `'[*See\n\n```\na\nb\n```\n\nnow*](http://x)'` | `'[*See*](http://x)\n\n```\na\nb\n```\n\n[*now*](http://x)'` |
| `<p><a href="http://x" title="T">See <code>a<br>b</code> now</a></p>` | `'[See\n\n```\na\nb\n```\n\nnow](http://x "T")'` | `'[See](http://x "T")\n\n```\na\nb\n```\n\n[now](http://x "T")'` |
| `<p><kbd><code>a<br># heading</code></kbd></p>` | `` '```` ```\na\n# heading\n``` ````' `` | `'```\na\n# heading\n```'` |
| `<p><em>x <code>a<br>b</code> y <code>c<br>d</code> z</em></p>` | `'*x\n\n```\na\nb\n```\n\ny\n\n```\nc\nd\n```\n\nz*'` | `'*x*\n\n```\na\nb\n```\n\n*y*\n\n```\nc\nd\n```\n\n*z*'` |
| `<p><em> <code>a<br>b</code> </em></p>` | `'*```\na\nb\n```*'` | `'```\na\nb\n```'` |
| `<p><em>Add <code>--gpus=1</code>.</em></p>` (guard) | `'*Add `--gpus=1`.*'` | unchanged |
| `<p>Before <code class="bash">a<br>b</code> after</p>` (guard) | `'Before\n\n```bash\na\nb\n```\n\nafter'` | unchanged |
| `<h1>Title</h1><p>Add <code>--gpus=1</code>.</p>` (guard) | `'# Title\n\nAdd `--gpus=1`.'` | unchanged |
| `<pre class="bash"><code>echo hi</code></pre>` (guard) | `'```\necho hi\n```'` | unchanged |
| `<p><code class="bash">#!/bin/bash<br />\n# comment<br />\necho hi</code></p>` (guard) | `'```bash\n#!/bin/bash\n# comment\necho hi\n```'` | unchanged |

Every "after" row parses with `markdown-it-py` 4.2.0 (`commonmark`) to exactly one fence
token and zero heading tokens.

## Risks / Trade-offs

- **Sibling text is emitted as two marked runs instead of one.** Accepted: an inline run
  cannot contain a block in CommonMark, and the issue asks for exactly this.
- **A split `<a>` gives two links with the same target.** Accepted, same reason; the
  alternative, one link with a block inside, is not a link at all.
- **The edge trim (D6) drops a space the browser also collapses at a block boundary.** It
  touches only the two text nodes that meet the cut.
- **A transparent wrapper between the `<code>` and a marked tag is not hoisted** (Non-Goals).
  Measured 0 in the sample.
- **Extra work per page:** one `while` loop per promoted block, bounded by the depth of its
  inline ancestors. Negligible against the parse `_promote_block_code` already does.
- **Recursion regression through the new code.** The helper is iterative and runs inside
  the guarded worker; the existing deep-nesting tests (`-k "deeply_nested or recursion"`)
  must stay green.

## Verification (for the close-out task and the PR body)

Run from the branch tip with the project environment. `markdown-it-py` is for verification
only. Do not import it in `src/` or `tests/`.

```bash
python - <<'EOF'
import sys; sys.path.insert(0, "")
from markdown_it import MarkdownIt
from src.data_manager.collectors.processing import html_to_markdown
md = MarkdownIt("commonmark")
for html in (
    '<p><em><code>a<br># heading</code></em></p>',
    '<p><strong><code class="bash">a<br># heading</code></strong></p>',
    '<p><a href="http://x">See <code>a<br>b</code> now</a></p>',
    '<p><strong>Note: <code>a<br>b</code> done</strong></p>',
    '<p><kbd><code>a<br># heading</code></kbd></p>',
    '<p><a href="http://x"><em>See <code>a<br>b</code> now</em></a></p>',
):
    toks = md.parse(html_to_markdown(html))
    fences = [t for t in toks if t.type == "fence"]
    heads = [t for t in toks if t.type == "heading_open"]
    assert len(fences) == 1 and not heads, (html, fences, heads)
print("PASS")
EOF
```

Corpus sample (network, about 1 minute). Re-derive the "0 of N" number for the PR body:

```bash
python - <<'EOF'
import re, random, time, urllib.request
from bs4 import BeautifulSoup, Tag
UA = {"User-Agent": "Mozilla/5.0 archi-probe"}
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25) as r:
        return r.read().decode("utf-8", "replace")
xml = get("https://docs.rc.fas.harvard.edu/sitemap.xml")
locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
pages = [u for u in locs if not u.endswith(".xml")]
for child in [u for u in locs if u.endswith(".xml")][:20]:
    pages += [u for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", get(child)) if not u.endswith(".xml")]
kb = sorted(set(u for u in pages if "/kb/" in u))
random.seed(399); sample = sorted(random.sample(kb, min(60, len(kb))))
MARKED = {"a","b","strong","em","i","del","s","kbd","samp","sub","sup"}
total = hits = 0
for u in sample:
    soup = BeautifulSoup(get(u), "html.parser")
    for c in soup.find_all("code"):
        if c.find_parent("pre") is not None or not c.find_all("br"):
            continue
        total += 1
        p = c.parent
        while isinstance(p, Tag) and p.name in MARKED | {"span","u","mark","small"}:
            if p.name in MARKED:
                hits += 1; break
            p = p.parent
    time.sleep(0.3)
print(f"kb pages={len(kb)} sampled={len(sample)} multi-line bare code={total} under marked inline ancestor={hits}")
EOF
```

Baseline measured 2026-09-02: `kb pages=213 sampled=60 multi-line bare code=25 under marked
inline ancestor=0`.
