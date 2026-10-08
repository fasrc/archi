## Context

`_joined_lines` already obeys one half of pip's comment rule: a whole-line comment never
OPENS a buffer (`:314`, from #527/#520). This change adds the other half: what pip does when a
comment line CLOSES a buffer that a previous line opened.

## Decision

pip 26.1.2 `join_lines`:

```python
if not line.endswith("\\") or COMMENT_RE.match(line):
    if COMMENT_RE.match(line):
        # this ensures comments are always matched later
        line = " " + line
    if new_line:
        new_line.append(line)
        ...
        yield primary_line_number, "".join(new_line)
```

So a comment line is always prefixed with a space; the prefix matters only when a buffer is
open (an unbuffered whole-line comment is dropped later either way). Mirror that branch: in
`_joined_lines`, when `buffered` is non-empty and `_COMMENT.match(raw_line)`, yield
`buffered + " " + raw_line` and reset the buffer. The comment then sits after whitespace, so
`_COMMENT.sub` cuts it and the pin before it reads.

Alternatives rejected:
- Always insert a separator on join: breaks the `--hash` control, where pip concatenates with
  no space.
- Drop the comment line: diverges from pip's logical-line text; other readers in the module
  (`:701`) see joined lines too.

## Risks

- The five monitored requirement files must parse identically. Baseline: 0 opaque lines; pin
  counts 1, 5, 104, 105, 109. The task re-checks them after the change.
- The new branch's comment must name pip 26.1.2, the `COMMENT_RE` branch of `join_lines`, and
  the measured output, per the issue.
