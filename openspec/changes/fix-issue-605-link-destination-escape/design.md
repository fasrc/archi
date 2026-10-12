## Context

`_ArchiMarkdownConverter.convert_a` (`src/data_manager/collectors/processing.py`) calls
`super().convert_a`, which returns one of two shapes:

- an autolink `<href>`, when the link text equals the `href` and there is no title;
- an explicit link `prefix[text](href "title")suffix` for every other link.

For the link that `_hoist_out_of_inline` keeps (attribute `_KEPT_LINK_ATTR`), the method
already rewrites an autolink with no URI scheme to `[href](href)` (issue #430 and #604).

CommonMark 0.31, section 6.3 ("link destination"): a bare destination has no ASCII
space or control character, and its unescaped parentheses must be balanced. A
destination in angle brackets `<...>` can hold spaces and any parentheses, but no line
ending and no unescaped `<` or `>`. A backslash escapes `<` and `>`.

## Goals / Non-Goals

**Goals:**
- Every link that `html_to_markdown` writes parses back to its original `href`.
- No change to the output for an `href` with no whitespace and balanced parentheses.

**Non-Goals:**
- No change to the link text or to the title part.
- No re-conversion of documents already on disk.
- No change for a line ending inside an `href`. HTML parsers keep it rarely, and no
  CommonMark destination form can hold it. The output stays as `markdownify` writes it.

## Decisions

### D1: A pure helper decides the destination form

Add `_link_destination(href: str) -> str` at module level, next to `_URI_SCHEME`. It
returns `href` unchanged when the bare form is valid. Else it wraps `href` in `<...>`
after four steps, in this order: double every `\`, backslash-escape `<` and `>`,
backslash-escape an `&` that leads a character reference, and write a NUL as `%00`
(a CommonMark reader replaces a literal NUL with U+FFFD before it parses; PR #686
review).

The bare form is valid when the `href` has no whitespace character (`str.isspace`
on each character, or an ASCII control character, code below 0x20 or 0x7F), and a scan of
its parentheses never goes below depth 0 and ends at depth 0. A DOM `href` has no
escapes of its own, so a `\` before ASCII punctuation or an `&` that leads a character
reference also makes the bare form invalid: a reader would consume or decode it (PR #686
review; this replaces the first draft, which counted `\)` as an escaped paren). A
leading `<` or a trailing `\` (which would escape the link's closing `)`) also makes it
invalid.

A pure helper is easy to test with exact strings and keeps `convert_a` small.

Alternative: escape each unmatched parenthesis with a backslash. Rejected: the
angle-bracket form is one rule for spaces and parentheses, and the issue's acceptance
criteria use it.

### D2: Rewrite the outer destination of the explicit-link output in place

When `_link_destination(href) != href`, find the outer destination by the complete
marker `"](" + href + title_part + ")"`, where `title_part` is `' "title"'` (with `"`
escaped as markdownify writes it) or empty. Search for the marker with `rfind`, from the
end of the output, and replace only its `href` with `_link_destination(href)`. The
title part and the prefix and suffix whitespace stay as they are.

A plain first-occurrence replace of `"](" + href` is wrong: the same sequence can occur
earlier, in the rendered link text. PR #686 review found two cases: an image inside the
link whose `src` equals the `href`, and a title that repeats `](href`. The outer
destination is the only occurrence that the title part and the closing `)` follow, and
it is the last one in the output.

Do not rebuild the link from parts. `markdownify` computes the text, title, prefix,
and suffix, and a rebuild would copy that logic.

### D3: An autolink that cannot hold its href becomes an explicit link

CommonMark forbids whitespace, `<`, and `>` in an autolink. When `super().convert_a`
returns `"<" + href + ">"` and the `href` has whitespace, `<`, or `>`, return
`"[" + _escape_link_label(href) + "](" + _link_destination(href) + ")"`. The text is
the `href`, as in the autolink, with its Markdown-active characters (`\`, `[`, `]`,
`<`, `>`, `` ` ``, `*`, `_`, and an `&` that leads a character reference)
backslash-escaped, because this branch writes the `href` as link text byte-for-byte.
Parentheses alone do not trigger this rule, because an autolink can hold them.

### D4: The kept link uses the same helper

Apply D2 and D3 to the result of the existing kept-link logic, at the end of
`convert_a`. The kept link `[href](href)` is an explicit link, so D2 rewrites it, and
`[/docs/foo)bar](</docs/foo)bar>)` is the result for the issue's fourth example. A
prototype of D1 to D4 on `origin/dev` `0ed50a23` gave the exact output of every scenario
in the spec delta.

## Risks / Trade-offs

- [Open PR #664 also edits `convert_a`] → The second PR to merge rebases. The two
  changes touch different lines: #664 changes which autolinks become explicit links, and
  this change changes how the destination is written.
- [A pinned test output changes] → Run the full converter test file. Record any changed
  pinned output in the PR body. No existing test is expected to change, because no
  existing test `href` has a space or unbalanced parentheses.
- [The `](href` sequence also occurs in the link text] → D2 matches the complete outer
  marker from the end, so an earlier occurrence in the text is never rewritten. Tests
  cover an image `src` equal to the `href` and a title that repeats `](href`.
- [`markdownify` does not escape an unbalanced `]` in link text] → A link whose text has
  an unbalanced `]` does not parse as a link. This is true for every link on `dev`,
  not only for the links that this change rewrites, and is out of scope here (the
  link text is a non-goal). The follow-up issue tracks it.
