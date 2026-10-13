# Design — relative and fragment self-links (#604)

## Context

`markdownify.MarkdownConverter.convert_a` (1.2.2):

```python
if '_noformat' in parent_tags:
    return text
prefix, suffix, text = chomp(text)
if not text:
    return ''
...
if (self.options['autolinks'] and text.replace(r'\_', '_') == href
        and not title and not self.options['default_title']):
    return '<%s>' % href
...
return '%s[%s](%s%s)%s' % (prefix, text, href, title_part, suffix) if href else text
```

The override today (`processing.py:695`) rewrites `<href>` to `[href](href)` only when the
anchor carries `_KEPT_LINK_ATTR`.

## D1 — Rewrite every scheme-less autolink

In `_ArchiMarkdownConverter.convert_a`:

```python
out = super().convert_a(el, text, parent_tags)
href = el.get("href") or ""
if (
    "_noformat" not in parent_tags
    and out == f"<{href}>"
    and not _URI_SCHEME.match(href)
):
    prefix, suffix, text = chomp(text)
    return f"{prefix}[{text}]({href}){suffix}"
return out
```

`chomp` is imported from `markdownify`. `_URI_SCHEME` (`processing.py:306`) already
exists. This is the same string that `markdownify`'s non-autolink branch writes, so the
link text keeps `markdownify`'s escapes (for example `\_`) and the whitespace around the
link is kept. Absolute URLs still match `_URI_SCHEME` and keep `<...>`.

Exact outputs (pinned by the tests):

| Input | Output |
|---|---|
| `<p><a href="/docs">/docs</a></p>` | `[/docs](/docs)` |
| `<p><a href="#sec">#sec</a></p>` | `[#sec](#sec)` |
| `<p><a href="docs/page.html">docs/page.html</a></p>` | `[docs/page.html](docs/page.html)` |
| `<p>see<a href="/docs"> /docs </a>now</p>` | `see [/docs](/docs) now` |
| `<p><a href="/a_b">/a_b</a></p>` | `[/a\_b](/a_b)` |
| `<p><a href="https://x/y">https://x/y</a></p>` | `<https://x/y>` (unchanged) |
| `<p><a href="mailto:a@b">mailto:a@b</a></p>` | `<mailto:a@b>` (unchanged) |
| `<p><a href="/docs">Docs</a></p>` | `[Docs](/docs)` (unchanged) |
| `<p><a href="/docs" title="T">/docs</a></p>` | `[/docs](/docs "T")` (unchanged) |

## D2 — Drop the kept-link marker

The kept link from `_hoist_out_of_inline` is a plain anchor whose text is its `title` or
`href`. Under D1 it gets the same treatment as any other self-link, so the marker has no
reader. Remove `_KEPT_LINK_ATTR` (constant and comment at `processing.py:300-302`) and the
`attrs={_KEPT_LINK_ATTR: ""}` argument in `_hoist_out_of_inline`. The existing tests
`test_hoist_anchor_with_relative_href_keeps_a_markdown_link` (`/docs`, `docs/page.html`,
`#section`) and the absolute-URL kept-link tests must pass unchanged.

One kept-link output changes: a relative `href` with an underscore, such as `/a_b`, was
`[/a_b](/a_b)` and becomes `[/a\_b](/a_b)`. Both render the same text in CommonMark; the new
form is what `markdownify` writes for every other link text. Pin it with a test.

## D3 — Leave `_noformat` contexts alone

Under `code`, `kbd`, `samp`, and `pre`, `convert_a` returns the raw text. Measured:
`<p><code><a href="/x">&lt;/x&gt;</a></code></p>` → `` `</x>` ``. That text equals
`<href>`, so without the `_noformat` guard D1 would turn it into `` `[</x>](/x)` ``. The guard
keeps the output byte-for-byte. Pin it with a test.

## Risks

- Corpus churn: only documents with a scheme-less self-link change, and only when they are
  new or force-overwritten.
- Issue #605 edits the same method. It must rebase on this change and extend D1, not add a
  second override.
