# Design — close the install-directive fail-open hole

## Context

`tests/unit/test_requirements_hygiene.py` guards five requirements files against a duckdb
declaration reappearing. Its parser has three layers: `_requirement_body()` strips comments
and drops non-requirement lines, `requirement_project_name()` resolves a PEP 503-normalized
name, and `declares_unreadable_requirement()` fails closed on anything that installs content
without a readable name. The hole: `_requirement_body()` drops **every** hyphen-led line, so
pip's install directives — which do pull installable content — never reach the fail-closed
layer. Issue #253 has the verified repro; PR #251's review thread
(discussion_r3788954250) is the origin.

## Decisions

### D1 — Enumerate the three install directives; everything else hyphen-led stays inert

pip's requirements-file syntax has exactly three directives that pull installable content
into the resolution: `-e`/`--editable`, `-r`/`--requirement`, `-c`/`--constraint`. The fix
recognizes those (space-separated, `=`-attached and, for the short forms, directly attached
values) and treats every other hyphen-led line as an inert option, exactly as today.

The alternative — an inert *allow-list* with fail-closed-on-unknown-flag — was rejected: it
would flag legitimately inert options the list forgot (`--no-binary`, `--prefer-binary`,
future pip flags), turning the guard into a maintenance burden and regressing the issue's
explicit "inert index options still pass unflagged" criterion. The directive set is small,
closed and stable; the inert set is open-ended. Enumerate the closed set.

A recognizable shape for the check (the implementer may adjust so long as the tests pass):

```python
_INSTALL_DIRECTIVE_PATTERN = re.compile(
    r"^-[erc]([=\s]|$)"          # -e URL, -r file, -c file, -e=URL, bare -e
    r"|^-[erc]\S"                # attached short form: -e./vendor/duckdb
    r"|^--(editable|requirement|constraint)([=\s]|$)"
)
```

Note `--requirement(...)` does not match `--require-hashes`, and `-[erc]` cannot match any
real two-hyphen inert flag.

### D2 — Report `-r`/`-c` targets as unreadable; do not follow them recursively

The issue offers both. Reporting is chosen: none of the five monitored files uses an include
today (verified — they carry only `--extra-index-url` lines), so recursion adds file I/O,
relative-path resolution and cycle handling to a guard for zero present benefit. The failure
message a maintainer sees tells them exactly what to do (pin by name, or extend the guard).
If a monitored file ever legitimately needs an include, extending the guard to scan the
target is the natural follow-up — and by then there is a concrete path to test against.

### D3 — Route directives through the existing unreadable-shape path; no second parser

`_requirement_body()` gains the directive check: an install-directive line **returns its
text** instead of `None`. From there the existing machinery does the right thing with no
further changes — the directive text (`-e git+...`) fails `_PROJECT_NAME_PATTERN`, so
`requirement_project_name()` returns `None` and `declares_unreadable_requirement()` returns
`True`. This keeps one parser and one fail-closed rule rather than a parallel
directive-detection branch in each public function, and it is what makes
`test_requirements_files_declare_only_readable_requirements` catch planted directives with
no change to that test.

### D4 — The `-r requirements-base.txt` parametrize case moves to the flagged list

`test_readable_requirement_shapes_are_not_flagged` currently includes
`-r requirements-base.txt` in its "not flagged" cases — that entry *encodes the fail-open
behaviour this change exists to remove*, so it moves to the flagged side (and gains
companions). The issue's "all 35 existing tests in the file stay green" criterion is read as
"no existing behaviour contract regresses except the one this issue overturns": the
same line also appears in `test_non_duckdb_declarations_are_not_detected`, where it stays
and stays green (`requirement_project_name` still returns `None` for it, which is `!=
"duckdb"`). Every other existing case is untouched.

### D5 — Spec delta uses `ADDED` requirements under `dependency-pin-hygiene`

The capability was introduced by `fix-issue-246-remove-dead-duckdb-pin`, which is merged but
**not yet archived** (issue #254 gates the archive on artifact reconciliation), so
`openspec/specs/dependency-pin-hygiene/` does not exist yet. A `MODIFIED` delta against a
spec that is not there cannot validate; the delta therefore `ADD`s new, distinctly named
requirements scoped to install directives. When both changes archive, the requirements merge
into the same capability without collision.

## Risks

- **Tree stays green**: the five monitored files carry only inert options today, verified
  before authoring — the new fail-closed rule fires on nothing in the tree.
- **Over-matching**: `-[erc]` with an attached value intentionally catches malformed
  single-hyphen spellings of long flags (e.g. `-editable ...`); pip would reject such a
  line, and failing closed on garbage in a monitored file is the guard's job, not a false
  positive.
