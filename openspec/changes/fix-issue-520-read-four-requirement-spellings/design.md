# Design — read four more requirement spellings as pip does

Every verdict below was measured on branch `fix/issue-520-read-four-requirement-spellings` at
`376b5867`, Python 3.11.15, pip 26.1.2. Where the issue body and the measurement disagree, the
measurement wins and the disagreement is stated.

## D1. Finding 1 — pip's comment rule inside `join_lines`, and the edge the issue gets wrong

`pip._internal.req.req_file.join_lines` reads, verbatim at pip 26.1.2:

```python
for line_number, line in lines_enum:
    if not line.endswith("\\") or COMMENT_RE.match(line):
        if COMMENT_RE.match(line):
            # this ensures comments are always matched later
            line = " " + line
        if new_line:
            new_line.append(line)
            yield primary_line_number, "".join(new_line)
            new_line = []
        else:
            yield line_number, line
    else:
        if not new_line:
            primary_line_number = line_number
        new_line.append(line.strip("\\"))
```

`COMMENT_RE` is `(^|\s+)#.*$` — the module's `_COMMENT` (`:379`) is the same regex, so no new
pattern is needed. Because `match` anchors at position 0, the alternation means a **whole-line**
comment, indented or not. An inline comment does not match: `vllm==0.9.0 # note` starts with
`v`, so `COMMENT_RE.match` is `None`.

Two rules follow, and the second is the one the issue body states incorrectly:

1. A line `COMMENT_RE` matches never **opens** a continuation buffer, even when it ends in `\`.
2. A line `COMMENT_RE` matches that arrives while a buffer is **open** is appended to that
   buffer, and the buffer is then flushed as one logical line. The issue body says pip *"yields
   the buffered requirement, then the comment"* — as two lines. It does not. Measured:

| input | pip `join_lines` output |
|---|---|
| `# comment \` , `vllm-0.9.0-py3-none-any.whl` | `[(1, ' # comment \\'), (2, 'vllm-0.9.0-py3-none-any.whl')]` |
| `   # comment \` , `vllm-0.9.0.whl` | `[(1, '    # comment \\'), (2, 'vllm-0.9.0.whl')]` |
| `#\` , `vllm-0.9.0.whl` | `[(1, ' #\\'), (2, 'vllm-0.9.0.whl')]` |
| `vllm==0.9.0 \` , `# note \` , `numpy==2.0.0` | `[(1, 'vllm==0.9.0  # note \\'), (3, 'numpy==2.0.0')]` |
| `vllm==0.9.0  # note \` , `numpy==2.0.0` | `[(1, 'vllm==0.9.0  # note numpy==2.0.0')]` |

The fourth row is rule 2: **one** logical line carrying the requirement with the comment
appended, then the following requirement on its own. The fifth row is the inline case, which
still continues — so the fix must key on `_COMMENT.match(...)`, never on `"#" in line`.

Consequence for the module: yielding the appended form is also the safe form, because
`_requirement_lines` (`:318`) runs `_COMMENT.sub("", line)` after stripping, which removes the
appended tail and leaves `vllm==0.9.0`. pip prepends a space to a comment line for exactly this
reason (its own comment says so); the module's `_requirement_lines` strips first and its
`startswith("#")` test then skips a whole-line comment, so the space is not needed here.

Keep the existing join mechanics unchanged: the current `_joined_lines` appends
`raw_line[:-1]`, which keeps the whitespace before the backslash. That reproduces pip's
`vllm==0.9.0 ; python_version < "3.11"` exactly (pip's own `# TODO: handle space after '\'`
records the same looseness), and D3 depends on it.

## D2. Why fix `_joined_lines` rather than `_opaque_requirements`

The wheel is reported by `_PATH_OR_ARCHIVE_REQUIREMENT` once it is a line of its own — that
pattern already works. The defect is upstream, in which physical lines become one logical line.
Fixing the reporter instead would have to re-split a joined line, which is the same parser
written twice, and it would leave `_parse_pins`, `_parse_requirements` and (after D3)
`_conditional_protected` still reading the wrong logical lines. One fix at the shared seam
repairs every reader at once.

## D3. Finding 3 — one iteration source

`_conditional_protected` (`:581`) walks `text.splitlines()`. A pin whose marker sits on the
next physical line therefore reads as unconditional: `_parse_pins` (via `_joined_lines`) sees
`vllm==0.9.0 ; python_version < "3.11"`, records the exact pin, and the conditional check sees
two unrelated lines and returns `{}`. Measured: `_parse_pins` → `{'vllm': '0.9.0'}`,
`_conditional_protected` → `{}`; the wanted answer is
`{'vllm': 'python_version < "3.11"'}`.

Swap the iteration source to `_joined_lines(text)` and change nothing else in the loop. Its
own `startswith("#")` and `startswith("-")` skips keep working, and after D1 a comment line
arrives whole rather than glued to the next requirement.

## D4. Finding 4 — one widened option pattern repairs two readers

`SUPPORTED_OPTIONS_REQ` at pip 26.1.2 is exactly `--hash` and `-C`/`--config-settings`;
measured, the only short per-requirement option pip has is `-C`. Both the attached spelling
`-Cfoo=bar` and the detached `-C foo=bar` are legal.

`_PER_REQUIREMENT_OPTION` (`:386`) is `\s+--\S+.*$`, so `-C` survives into the line the readers
match. Measured at `376b5867`, for each of `vllm==0.9.0 -Cfoo=bar`, `vllm==0.9.0 -C foo=bar`
and the continued `vllm==0.9.0 \` + `    -Cfoo=bar`:

- `_parse_pins` → `{}` (the anchored `_PIN_PATTERN` fails at `$`), so vllm reads as carrying
  no exact pin;
- `_unpinned_protected` → `{'vllm': '==0.9.0 -Cfoo=bar'}` and its siblings, a red suite on a
  line that is a legitimate exact pin.

Both symptoms come from the one cut, because `_parse_pins` and `_parse_requirements` both read
`_requirement_lines`, which applies the pattern. Widen it to cut a `-C` too —
`\s+(?:--\S+|-C(?:\s|\S)).*$` or any equivalent. The `(?:\s|\S)` requires a character after
`-C` so a bare trailing `-C` is not silently cut; no project name can be caught by the `-C`
branch, because the pattern needs leading whitespace and `_NAME` must start with an
alphanumeric. Do not generalise to all short options: pip has no others, and a looser pattern
would start eating requirement text.

## D5. What stays untouched, and why

`_conditional_protected` cuts its comment with `line.split("#", 1)[0]` while
`_requirement_lines` cuts with `_COMMENT` (pip's rule). The two disagree on `foo#vllm.tar.gz`,
where the `#` belongs to the requirement. That asymmetry is real, it is **not** one of the four
findings, and it was not measured for this change: repairing it would change which lines the
conditional check reads, which is a verdict change this change cannot evidence. Leave it, and
leave `_COMMENT`, `_PIN_PATTERN`, `_REQUIREMENT_PATTERN` and `_ARCHIVE_SUFFIX` alone.

## D6. Spec delta direction

`ls openspec/specs/` carries no `dependency-pin-hygiene` capability; the capability exists only
in unarchived changes, `fix-issue-491-opaque-local-path-requirements` among them. So the delta
uses `## ADDED Requirements`. Using `## MODIFIED` would fail `openspec validate --strict`,
which resolves a modified requirement against an archived spec.

## D7. Finding 2 — a detached extras list is part of the requirement, not the filename

`_PATH_OR_ARCHIVE_REQUIREMENT` (`:426`) already steps over an **attached** extras list through
`_ATTACHED_EXTRAS` (`:425`), then demands `;`, end of line, or whitespace **not** followed by a
comparison. With a detached list the lookahead sees `[`, so `example.zip [foo] ==1.0` is
reported. Measured: `_opaque_requirements('example.zip [foo] ==1.0')` →
`['example.zip [foo] ==1.0']` and `'example.tar [foo] (>=1)'` likewise, while pip reads the
project `example.zip` with extras `foo` and specifier `==1.0`.

This is the same whitespace asymmetry round 5 of #506 fixed for `@` with
`_NAME_WITH_SPACED_EXTRAS` (`:448`), and it is fixed the same way: permit an optional
`\s*\[[^\]]*\]\s*` between the suffix and the comparison lookahead. The widening stays inside
this one pattern, so no pin is read across a space.

Two negatives must survive the widening, and they are the test that the fix is not too wide:
`example.zip [foo]` and `example.zip` carry no comparison, so both stay reported. Measured at
`376b5867`: both already report, and they must still report afterwards.

## D8. Order of work

Findings 1 and 3 hide a protected package; 2 and 4 fail legitimate work. Land them
hiding-first (1, 3, 4, 2) so the higher-risk readings close earliest, and so finding 3's fix
lands on top of finding 1's corrected `_joined_lines` rather than the reverse.

## D9. Acceptance oracle

The probe in the issue body is the oracle, and it must print `0 checks give the wrong verdict`.
One caution when writing an extra check of your own: `_joined_lines` is a **generator**, so
compare `list(_joined_lines(text))`, never the generator object. A probe written against the
bare call reports a spurious mismatch that is an artefact of the probe and not a defect.
