# Close the duckdb guard's fail-open hole on pip install directives

## Why

The duckdb reintroduction guard added by PR #251 (`tests/unit/test_requirements_hygiene.py`)
fails open on pip **install directives**. `_requirement_body()` skips every line that begins
with a hyphen:

```python
if not text or text.startswith("-"):
    return None
```

That is correct for inert options (`--extra-index-url`-style flags, which the monitored
header files legitimately carry) but wrong for the three requirements-file directives that
pull installable content into the resolution: editable (`-e`/`--editable`), requirement
(`-r`/`--requirement`) and constraint (`-c`/`--constraint`). Reproduced on `origin/dev` at
`cdd6e35d`:

```
'-e git+https://host/duckdb.git#egg=duckdb'  -> requirement_project_name=None  declares_unreadable_requirement=False
'--editable git+https://host/duckdb.git'     -> None  False
'-r extra-requirements.txt'                  -> None  False
```

Both the duckdb name check and the fail-closed unreadable-shape check stay green while pip
installs duckdb. This is the same fail-open class PR #251's round-2 change set out to close
— the blanket hyphen skip exempted an entire syntax class from the fail-closed rule. The
guard is still a strict improvement over having no guard (issue #253 calls this a
completeness gap, not a regression), but the gap is a real bypass and was verified against
the merged code before filing.

## What Changes

- Split hyphen-led lines into two categories inside the guard module
  (`tests/unit/test_requirements_hygiene.py` — a tests-only change, no `src/` edits):
  1. **Inert options** — index URLs, find-links, trusted hosts, hashes and any other flag
     that pulls in no installable content. These keep being skipped, exactly as today.
  2. **Install directives** — `-e`/`--editable`, `-r`/`--requirement`, `-c`/`--constraint`
     (space-, `=`- and attached-value forms). These are no longer silently skipped: they
     report as unreadable shapes, so `test_requirements_files_declare_only_readable_requirements`
     fails closed on them, consistent with how bare VCS/URL/path requirements are already
     handled.
- Requirement and constraint includes are **reported, not followed recursively** — no
  monitored file uses one today, so recursion buys nothing yet (design D2).
- New parametrized test cases cover, at minimum: an editable VCS directive with an egg
  fragment, an editable local path, and a recursive requirements include — written red
  first, per TDD.
- The one existing parametrize case that encodes the fail-open behaviour
  (`-r requirements-base.txt` in `test_readable_requirement_shapes_are_not_flagged`) moves
  to the flagged list — see design D4 for why this is the issue's intent and not a
  regression of its "existing tests stay green" criterion.
- The module docstring and `declares_unreadable_requirement`'s docstring state the guard's
  actual coverage — which shapes are read, which option lines are skipped as inert, and
  that includes are reported rather than followed. No "every shape" claim the code does not
  deliver.

Explicitly **not** done:

- No recursion into `-r`/`-c` targets (design D2; revisit if a monitored file ever
  legitimately needs an include).
- No change to which files are monitored — that is issue #278's manifest work.
- No edit to the fix-issue-246 change artifacts — reconciling those is issue #254.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `dependency-pin-hygiene`: the guard's fail-closed rule now extends to pip install
  directives, so an editable VCS line, a local-path editable or a requirements/constraints
  include in a monitored file fails the suite instead of passing unread. The delta is
  expressed as `ADDED` requirements because this capability's spec has not yet been
  archived into `openspec/specs/` — it exists only in the unarchived
  `fix-issue-246-remove-dead-duckdb-pin` change (see design D5).

## Impact

- **Tests**: `tests/unit/test_requirements_hygiene.py` only — the guard's two helper
  functions, new parametrized cases, one relocated parametrize case, docstring updates.
- **Runtime / src**: none. No production code path changes.
- **Monitored files**: unchanged, and they stay green — the five files carry only inert
  `--extra-index-url` options today (verified on `cdd6e35d`), no install directives.
- **Coverage**: the diff is tests-only, so diff-cover reports no measurable `src/` lines
  ("no lines with coverage information"). That is a legitimate pass, not a bypassed gate.
- **Related issues**: #278 (monitored-file-set manifest) and #254 (artifact reconciliation)
  are independent and stay independent; #254 should reconcile *after* this lands so the
  archived design records the final guard shape, as its own body requests.
