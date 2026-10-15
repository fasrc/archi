## Why

`html_to_markdown` writes link text as markdownify renders it. markdownify escapes `[`
and `]` only when `escape_misc=True`, and the project converter runs with the default
`escape_misc=False` (`src/data_manager/collectors/processing.py`, `_markdownify_deep_safe`).
CommonMark 0.31 lets link text hold brackets only when they are balanced, so an
unbalanced `]` ends the label early and the whole line reads back as text, not a link
(issue #691). Measured on `origin/dev` `d0559c14`:

- `<p><a href="https://x/y">a ] b</a></p>` → `[a ] b](https://x/y)` (not a link)
- `<a href="https://x/a  ]b">https://x/a  ]b</a>` → `[https://x/a ]b](<https://x/a  ]b>)`
  (markdownify collapses the double space, so the text no longer equals the `href` and
  the self-link label escape `_escape_link_label` is not used)
- `<a href="https://x/a ] b" title="T">https://x/a ] b</a>` →
  `[https://x/a ] b](<https://x/a ] b> "T")` (a titled self-link takes the explicit-link
  branch)
- `<p><a href="x">a [ b</a></p>` → `[a [ b](x)` (an unmatched `[` also breaks the label)

PR #686 (issue #605) fixed the link destination and listed the link text as a non-goal.
Its review found these cases one branch at a time; the common cause is that link text
with an unbalanced bracket is never escaped, for any link. This change fixes it once.

## What Changes

- A new helper `_escape_unbalanced_brackets(text)` in
  `src/data_manager/collectors/processing.py` puts a backslash before each `[` or `]` in
  already-rendered Markdown text that is unbalanced, not already backslash-escaped, and
  not inside a code span.
- `_ArchiMarkdownConverter.convert_a` applies it to the link text of every explicit link
  (`[text](dest)`, with or without a title), before the destination rewrite of issue #605.
- Balanced brackets, nested images (`[![pic](/a)](/b)`), code spans, and already-escaped
  brackets in link text do not change. A `<href>` autolink does not change. The
  scheme-less self-link branch keeps `_escape_link_label` (it escapes more characters,
  because its text is a literal `href`, not Markdown).
- One bullet in `docs/docs/configuration.md` in the HTML-to-Markdown list.

## Capabilities

### New Capabilities

### Modified Capabilities

- `ingest-processing`: adds a requirement that link text with an unbalanced bracket
  round-trips through CommonMark as a link.

## Impact

- Code: `src/data_manager/collectors/processing.py` (`convert_a` and one new helper).
- Tests: `tests/unit/test_html_to_markdown_processor.py`.
- Docs: `docs/docs/configuration.md`.
- Data: like every conversion change, the new form reaches disk only for new or
  force-overwritten documents. Links with balanced (or no) brackets in their text, the
  usual case, convert byte-for-byte as before.
