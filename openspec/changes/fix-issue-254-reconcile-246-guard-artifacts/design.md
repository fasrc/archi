## Context

`openspec/changes/fix-issue-246-remove-dead-duckdb-pin/` is unarchived. Its `design.md`,
`tasks.md` and `specs/dependency-pin-hygiene/spec.md` were written before review reshaped the
guard, and PR #251 merged the reshaped guard without updating them.

What actually shipped, in `tests/unit/test_requirements_hygiene.py` on `dev`:

| Artifact says | Code does |
| --- | --- |
| three monitored paths | five: `requirements/requirements-base.txt`, `requirements/cpu-requirementsHEADER.txt`, `requirements/gpu-requirementsHEADER.txt`, and the two `src/cli/templates/dockerfiles/base-*-image/requirements.txt` |
| literal line match `^\s*duckdb([=<>!~ ]|$)` | `requirement_project_name()` reads the project name, normalizes it per PEP 503, and compares it to `duckdb` |
| (not recorded) | `declares_unreadable_requirement()` fails the suite closed on a requirement whose project name cannot be read |
| (not recorded) | the monitored set must equal the set of requirements paths the image generator references, compared both ways |

The header files matter because `scripts/dev/build_docker_images.sh` concatenates them ahead
of `requirements-base.txt` to produce the two image requirements files. A `duckdb` line added
to a header leaves the tracked outputs — and a three-path guard — green until the next
base-image build regenerates them, which is the moment it gets installed.

## Goals / Non-Goals

**Goals**: make #246's guard-design statements describe the shipped guard; record the two
shipped obligations the artifacts omit entirely; keep the change reviewable as documentation.

**Non-Goals**: changing the guard's behaviour or any source or test file; archiving #246;
widening the pin-deletion narrative; closing the guard's known completeness gap (#253) or
deriving the monitored set from a shared manifest (#278).

## Decisions

### D1 — Edit only the guard-design statements, never the pin-deletion narrative

`design.md` counts "three" in two unrelated senses. Three files carried the pin, and the diff
that deleted it was three lines — that is the pin-deletion narrative at `design.md:3`,
`design.md:33` and `design.md:64`, and it is still true. The guard, separately, watches five
files. A find-and-replace over "three" would corrupt accurate history to fix inaccurate
design, so each anchor is edited by hand:

- `design.md` D3 (the matching rationale, around `:89-104`)
- `design.md` D5 (the collection behaviour, around `:116-124`)
- `tasks.md` task 1.1 (the path list and the pattern, `:3-22`)
- `spec.md` — the guard requirement and its scenarios only

The `design.md:7-11` diagram already shows the two header files as the generator's inputs, so
widening the guard's scope to five paths agrees with the Context section rather than
contradicting it.

### D2 — Drop the "agrees character for character" claim rather than restate it

D3 currently argues that the guard and the loop image's duckdb-stripping filter should agree
character for character on what counts as a duckdb pin. They no longer do, and they should
not: the filter's character class stops at `[=<>!~ ]`, so it would pass `duckdb[httpfs]==1.0`
through, while the guard catches it. The reconciled D3 states the relationship that now
holds — the guard is deliberately the stricter of the two, and it fails the suite before such
a declaration can reach an image build, so the day the filter is deleted the guard is already
enforcing the stronger condition. The filter itself stays untouched; it is control-plane and a
human follow-up, exactly as D4 already records.

### D3 — Reconcile #246's spec delta too, in its own commit

Issue #254 names `design.md` and `tasks.md`. `spec.md` is the file `openspec archive` promotes
into `openspec/specs/`, and its second requirement bounds the guard to "any of the three
shared base requirements files" — guard scope, not pin deletion. Promoting that sentence makes
the drift durable, which is the outcome the issue exists to prevent. So it is reconciled here,
isolated in its own commit so a reviewer who reads the issue narrowly can drop it alone.

Its first requirement ("SHALL NOT pin duckdb" in the three named files) and its last
("SHALL NOT regenerate the derived requirements files", three deleted lines) are pin-deletion
statements and stay exactly as they are.

### D4 — This change's delta adds only what is recorded nowhere

`dependency-pin-hygiene` is not in `openspec/specs/` yet — #246 is unarchived — so a delta
against it must use `ADDED Requirements`; `MODIFIED` would reference a spec that does not
exist and fails validation. To avoid duplicating requirements this change is at the same time
correcting in place, the delta adds only the two obligations no artifact records at all:

1. the monitored set equals the set of requirements files the generator references, and
2. an unreadable requirement line fails the suite closed.

Both are shipped, both are covered by tests on `dev`, and neither appears in #246's spec.

### D5 — Verification is a text-and-code comparison, not a new test

There is nothing to run red. The check is that every guard-design statement in the reconciled
artifacts can be pointed at a line of `tests/unit/test_requirements_hygiene.py`, and that
`git diff` touches Markdown only. The gate still runs in full before each commit; its
diff-coverage step will report no lines with coverage information, which is the expected
result for a Markdown-only diff.

## Risks / Trade-offs

- **#253 is in flight and extends the guard.** PR #298 closes a fail-open hole for pip option
  lines. To avoid re-drifting the moment it merges, the reconciled text describes the guard's
  contract — normalized-name comparison, fail-closed on unreadable shapes, bidirectional path
  discovery — without enumerating which unreadable shapes are covered, and points at #253 for
  the known gap. If #298 merges first, the reconciled text stays true.
- **Scope beyond the issue's literal criteria** (D3). Mitigated by the isolated commit and by
  stating it in the PR body.
- **No executable check protects this.** Artifacts can drift again; #278 tracks deriving the
  monitored set from a manifest shared with the generator, which is the structural fix.
