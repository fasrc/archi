# Reconcile the issue-246 guard artifacts with the guard that shipped

## Why

PR #251 landed the duckdb reintroduction guard, but the change artifacts of
`openspec/changes/fix-issue-246-remove-dead-duckdb-pin/` were never updated to follow the
review that reshaped it. They now prescribe a design that review deliberately rejected:
three monitored paths and a literal `^\s*duckdb([=<>!~ ]|$)` line match.

The guard that shipped monitors **five** paths — `requirements/requirements-base.txt`, the
two `requirements/{cpu,gpu}-requirementsHEADER.txt` generator inputs, and the two
`src/cli/templates/dockerfiles/base-*-image/requirements.txt` outputs — and compares
**PEP 503-normalized project names** instead of matching a literal prefix. The literal
pattern was replaced because it misses `DuckDB==1.0` (case), `duckdb[httpfs]==1.0` (extras)
and `duckdb; python_version >= "3.11"` (marker), all of which install the same distribution.
The shipped guard also carries two obligations the artifacts never recorded at all: it fails
closed on a requirement line whose project name it cannot read, and it asserts that its
monitored set equals the set of requirements files the image generator references.

These artifacts are the instructions a maintainer or a later verification pass follows.
A reader doing exactly as told would reintroduce the case, extras, marker and header blind
spots that PR #251 closed. This has to be reconciled **before `openspec archive`**, because
archive promotes the change's `specs/` delta into `openspec/specs/` and turns the drift into
the durable record.

## What Changes

Documentation only. No source or test file changes, and no behaviour change to the guard.

- **`fix-issue-246-remove-dead-duckdb-pin/design.md` — D3 and D5 only.** Restate D3's
  matching rationale in terms of normalized project names, drop the claim that the guard
  must agree character-for-character with the loop image's duckdb-stripping filter, and
  restate D5's collection behaviour over the five monitored paths.
- **`fix-issue-246-remove-dead-duckdb-pin/tasks.md` — task 1.1 only.** Replace the
  three-path list with the five shipped paths and the literal regex with the normalized-name
  comparison, keeping the red-then-green instruction intact.
- **`fix-issue-246-remove-dead-duckdb-pin/specs/dependency-pin-hygiene/spec.md` — the guard
  requirements only.** The requirement and scenarios that bound the guard to "any of the
  three shared base requirements files" describe guard coverage, not the pin deletion, and
  this file is the one archive promotes. They are widened to the monitored set.
- **This change's own spec delta** records the two shipped obligations #246's spec omits:
  monitored-set-equals-generator-set, and fail-closed on an unreadable requirement line.

Deliberately left intact, because they are accurate: the pin-deletion narrative that counts
**three** files (`design.md:3`, `design.md:33`, `design.md:64`; the "SHALL NOT pin duckdb"
requirement; the "exactly three deleted lines" requirement). The pin was carried by three
files and the diff that removed it was three deleted lines. Only the **guard-design**
statements contradict the shipped code.

## Scope note

Issue #254's acceptance criteria name `design.md` and `tasks.md:1.1`. This change also edits
the guard requirements in #246's `specs/dependency-pin-hygiene/spec.md`, which the issue does
not name. That file is precisely what `openspec archive` promotes into `openspec/specs/`, so
leaving its "three files" guard scope in place would defeat the stated reason for the issue —
the drift would become the durable record. The edit is confined to guard scope and matching
mechanism; a reviewer who wants the narrower reading can drop that one commit without
disturbing the rest.

## Impact

- Affected specs: `dependency-pin-hygiene` (not yet archived into `openspec/specs/`, so this
  change's delta uses `ADDED Requirements`).
- Affected code: none. `tests/unit/test_requirements_hygiene.py` already implements
  everything described here; this change makes the record match it.
- The gate's diff-coverage step reports no lines with coverage information, because the diff
  touches only Markdown. That is a legitimate pass, not a bypass.
