## Context

`html_to_markdown(html)` (`src/data_manager/collectors/processing.py:212`) calls
`_markdownify_deep_safe(html)` (`:662`), which converts `_promote_block_code(html)` (`:441`)
with markdownify. `_promote_block_code` already parses the page with BeautifulSoup (`:453`)
and returns `str(soup)`. Measured on origin/dev `26e6429e`:

| input | output now |
|---|---|
| `<h3></h3><p>x</p>` | `'### \n\nx'` |
| `<h2>   </h2><p>x</p>` | `'## \n\nx'` |
| `<h1><span></span></h1><p>x</p>` | `'# \n\nx'` |
| `<h2>&nbsp;</h2><p>x</p>` | `'## \n\nx'` |
| `<h4><br></h4><p>x</p>` | `'#### \n\nx'` |
| `<h3>Title</h3>` | `'### Title'` |
| `<h3><code>foo</code></h3>` | ``'### `foo`'`` |
| `<h3><img src='a.png' alt='A'></h3>` | `'### A'` |

## Goals / Non-Goals

Goals: an empty heading leaves no `#` line. A heading with text, or with an `img` or `code`
descendant, renders exactly as before.

Non-Goals: a configuration option; the dormant Markdown chunking path (`node_parsing.py`);
bank edits; the order or content of any other transform.

## Decisions

**D1 — Where.** A new module-level helper `_drop_empty_headings(soup)` mutates the soup in
place and returns `None`. `_promote_block_code` calls it once, right after it parses the page
(`soup = BeautifulSoup(html, "html.parser")`) and before the `<code>` loop. One parse carries
both transforms. Update the `_promote_block_code` docstring with one sentence that names the
call and issue #400. Do not change `html_to_markdown`, `_markdownify_deep_safe`,
`HtmlToMarkdownProcessor`, or `goldenset_maintenance.py`.

**D2 — The rule.** For each tag in `soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])`:
decompose it when `tag.get_text(strip=True) == ""` AND `tag.find(["img", "code"]) is None`.
Python's `str.strip` removes `\xa0`, so `&nbsp;` counts as empty. A `<br>` has no text, so
`<h4><br></h4>` is dropped.

**D3 — `img` and `code` keep the heading even when they are empty**, as the issue body says.
`<h3><img src='a.png'></h3>` (no alt) still renders `### ` after this change. That case is out
of scope; do not widen the rule.

**D4 — Decompose, not unwrap.** An empty heading has no content to keep, so `decompose()`
removes it and its empty children.

**D5 — Digests.** The golden-set drift check hashes `html_to_markdown` output, so pages with
empty headings get a new digest. This is expected (issue #400 "Decision"). No test in the repo
pins a digest for a page with an empty heading (`grep -rn '<h[1-6]></h' tests/` is empty on
`26e6429e`), so no test fixture changes.

## Risks / Trade-offs

- A heading that is empty in HTML but styled by CSS as a visual divider disappears. It had no
  text, so retrieval loses nothing.
- `_promote_block_code` is tested directly. Its output for input with no empty heading is
  unchanged, so the existing direct tests stay green.
