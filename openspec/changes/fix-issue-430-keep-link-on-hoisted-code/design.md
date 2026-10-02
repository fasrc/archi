# Design — keep the link of an anchor that the hoist empties

## Context

`_promote_block_code` (`src/data_manager/collectors/processing.py:445`) wraps each bare
multi-line `<code>` in a `<pre>` and then calls `_hoist_out_of_inline(pre, soup)` for each
block in reverse order (`processing.py:481-482`). `_hoist_out_of_inline`
(`processing.py:418`) walks up while the parent is in `_INLINE_MARKUP_TAGS`. At each level
it moves the siblings after the `<pre>` into a clone (`tail`), moves the `<pre>` after the
parent, trims the cut whitespace, inserts `tail` if `_has_content(tail)`, and decomposes the
parent if `not _has_content(parent)` (`processing.py:438`).

`markdownify` 1.2.2 `convert_a` renders `<a href="h">t</a>` as `<h>` when `t == h` (with
`\_` unescaped) and there is no title, else as `[t](h "title")`. The project converter keeps
the default `autolinks=True`.

## Decisions

### D1. Replace the empty anchor in place, only when both halves are empty

The new branch runs after the split and trim, when `parent.name == "a"`,
`parent.get("href")` is non-empty, `not _has_content(tail)`, and
`not _has_content(parent)`. It builds `link = soup.new_tag("a", href=parent["href"])`, sets
`link.string` to the text from D2, and calls `parent.replace_with(link)`. The `<pre>` sits
directly after the parent, so the link precedes the fence. The loop then continues upward:
the `<pre>`'s parent is unchanged, so an outer marked ancestor is split as before.

The check needs the tail: if the tail has content, the tail already carries the `href`
(`'<p><a href="http://x"><code>a<br>b</code> now</a></p>'` converts to
`` ```\na\nb\n```\n\n[now](http://x) ``), and an extra link would duplicate it.

Measured with a probe of this branch on `570814d0`:

| Input `<p>…</p>` | Output |
|---|---|
| `<a href="https://x/y"><code>a<br>b</code></a>` | `` <https://x/y>\n\n```\na\nb\n``` `` |
| `<a href="https://x/y" title="Docs"><code>a<br>b</code></a>` | `` [Docs](https://x/y)\n\n```\na\nb\n``` `` |
| `<a href="https://x/y"><em><code>a<br>b</code></em></a>` | `` <https://x/y>\n\n```\na\nb\n``` `` |
| `<a href="http://x"> <code>a<br>b</code> </a>` | `` <http://x>\n\n```\na\nb\n``` `` |
| `<a href="http://x"><!-- c --><code>a<br>b</code></a>` | `` <http://x>\n\n```\na\nb\n``` `` |
| `<a href="http://x"><code>a<br>b</code><code>c<br>d</code></a>` | `` <http://x>\n\n```\na\nb\n```\n\n```\nc\nd\n``` `` |
| `<strong><a href="http://x"><code>a<br>b</code></a></strong>` | `` **<http://x>**\n\n```\na\nb\n``` `` |
| `<a><code>a<br>b</code></a>` | `` ```\na\nb\n``` `` (unchanged) |
| `<a href="http://x"><code>a<br>b</code> now</a>` | `` ```\na\nb\n```\n\n[now](http://x) `` (unchanged) |
| `<a href="http://x">See <code>a<br>b</code></a>` | `` [See](http://x)\n\n```\na\nb\n``` `` (unchanged) |

The two-block row gives one link before the first block because the hoist runs in reverse
order: the second block is hoisted first, while the first block still holds the head half
open, so only the last hoist sees both halves empty.

The issue's acceptance criterion writes the untitled result as `[https://x/y](https://x/y)`.
`markdownify` renders a link whose text equals its `href` as the autolink `<https://x/y>`,
which CommonMark parses as the same link. The intent ("a Markdown link with text
`https://x/y` before the fence") holds; the tests pin the autolink string.

### D2. The link text is the stripped title, else the href

`(parent.get("title") or "").strip() or parent["href"]`. A blank title falls back to the
`href`. The new tag carries only `href`: copying `title` would render
`[Docs](https://x/y "Docs")`, which repeats the title.

### D3. A kept link with a relative href uses explicit link syntax

`markdownify` writes `<href>` when the link text equals the `href`. A CommonMark autolink
needs a URI scheme, so `<a href="/docs"><code>a<br>b</code></a>` would give `</docs>`,
which reads as an HTML end tag, not a link. `_hoist_out_of_inline` marks the link it keeps
with `data-archi-kept-link`. `_ArchiMarkdownConverter.convert_a` gives a marked link whose
`href` has no scheme the form `[href](href)`. Absolute targets stay `<https://x/y>`.

Other self-links on a page (`<a href="/docs">/docs</a>`) have the same defect on `dev`.
They are not changed here, because that change reaches every relative self-link in the
corpus. Issue #604 tracks it.

### D4. Tests pin exact strings and the tree

Each scenario in this change's spec gets an `html_to_markdown` exact-string test. One
`_promote_block_code_soup` test pins the tree for the nested case: exactly one `<a>`, no
`<em>`, the `<a>` is the `<pre>`'s previous sibling, and the `<pre>`'s parent is the `<p>`.
The neighbour tests at `tests/unit/test_html_to_markdown_processor.py:806` and `:819` stay
unchanged and green.

## Risks / Trade-offs

- The link sits on its own paragraph before the fence. That is the operator's choice (B).
- No corpus digest moves: the #406 review found zero pages with this shape.
