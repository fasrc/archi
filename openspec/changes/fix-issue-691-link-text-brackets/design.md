## Context

`_ArchiMarkdownConverter.convert_a` (`src/data_manager/collectors/processing.py`) calls
markdownify 1.2.2 `MarkdownConverter.convert_a`, which returns one of:

- `text` unchanged in a `_noformat` context (inside a code span or block);
- `<href>` (autolink) when the chomped text equals the `href` and there is no title;
- `prefix[text](href "title")suffix` otherwise.

The project override then (a) rewrites a scheme-less or unholdable autolink into
`[label](dest)` with `label = _escape_link_label(href)`, and (b) rewrites the outer
destination with `_link_destination(href)` (issue #605). The link `text` it receives is
already Markdown: it can hold a nested image `![alt](src)`, a code span, or an escaped
`\]`. Nothing escapes an unbalanced bracket in that text today.

## Goals / Non-Goals

**Goals**
- Every explicit link written by `convert_a` has link text in which each bracket is
  either balanced or backslash-escaped, so CommonMark 0.31 parses the line as a link.
- Byte-for-byte unchanged output for link text with no unbalanced bracket.

**Non-Goals**
- Turning on `escape_misc=True` (it escapes `\ & < > ` ~ = + |` and line-leading
  `-`, `#`, `1.` in ALL text; not measured, out of scope).
- Changing destination logic (`_link_destination`) or `_escape_link_label`.
- Changing `<href>` autolinks: an autolink has no label, and `]` is valid inside it.
- Nested links inside link text (CommonMark forbids them; markdownify never writes one
  from valid HTML).
- Text in a `_noformat` context: it is returned before any escape, as today.

## Decisions

### D1. Helper `_escape_unbalanced_brackets(text: str) -> str`
Add it next to `_escape_link_label`. One left-to-right scan over `text`:

1. `\` followed by any character: copy both characters; the second is never counted.
   A trailing lone `\` is copied as is.
2. A backtick run of length `n`: look for the next backtick run of exactly length `n`.
   If found, copy the whole code span verbatim (its brackets are never counted). If not
   found, copy the run as literal text and continue after it (CommonMark rule).
3. `[`: push its output position on a stack.
4. `]`: pop the stack if it is not empty (a balanced pair); else mark this `]`.
5. Any other character: copy it.

At the end, mark every `[` still on the stack. Return `text` with a `\` inserted before
each marked bracket. When nothing is marked, return `text` itself (same object), so the
caller can test `safe is text` or `safe == text`.

Why a stack and not a count: `] [` has equal counts but both brackets are unbalanced.

### D2. Where `convert_a` applies it
After the `_noformat` early return and after the `<href>` autolink branch (which
returns), and BEFORE `dest = _link_destination(href)`:

```python
safe = _escape_unbalanced_brackets(text)
if safe != text:
    out = super().convert_a(el, safe, parent_tags)
```

Re-render through the base converter instead of string-splicing `out`, because the base
converter chomps the text and builds the title part; a re-render keeps one owner of that
shape. The destination rewrite that follows finds the outer `](href title)` marker with
`rfind`, so it still targets the outer destination. The re-render is only reached when
the first render was not the `<href>` form.

If the re-render returns the `<href>` form (only when the escaped text equals the `href`
byte-for-byte, that is the `href` itself holds `\]` or `\[`), accept it and return it
through the same scheme-less check as the autolink branch above. Implement this as a
small loop or by moving the autolink branch below the re-render; keep one copy of the
autolink logic. A test for this edge is optional; do not add a requirement for it.

### D3. Docs
One bullet in `docs/docs/configuration.md`, in the HTML-to-Markdown list after the
issue #605 bullet ("A link target that Markdown would misread is escaped."): link text
with an unbalanced `[` or `]` (outside a code span, not already escaped) gets a
backslash before that bracket so the link survives (issue #691); balanced brackets and
nested images are unchanged; like every item in the list, it reaches disk only for new
or force-overwritten documents.

## Risks / Trade-offs

- A code-span rule mismatch with CommonMark could leave a bracket unescaped inside an
  odd backtick sequence. The tests pin the unmatched-run case (D1 step 2).
- The number of FASRC KB links with an unbalanced bracket is not measured in the repo.
  Task 1.1 measures it over the test fixtures and any local KB HTML it can read, and the
  PR body records the number. A low number does not change the fix: the defect is a
  silent lost link, and the fix is a no-op for all other links.

## Measurement

Scanned `tests/unit/test_html_to_markdown_processor.py` — the only source of HTML
fixtures in this repo (no local KB HTML is accessible without secrets).

- Total `<a>` elements found in HTML strings passed to `html_to_markdown`: **57**
- Links whose `get_text()` contains an unbalanced bracket: **1**
  - Sample: `'https://x/a ] b'` — an existing self-link test fixture that already
    exercises the titled self-link scenario.

No test output changed as a result of implementing the fix.
