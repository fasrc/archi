# Make the release workflow's base-image retarget step do its job

## Why

The release workflow retargets the 15 service Dockerfile templates at the base images the
`build-images` job just published, then smoke-tests the result. The retarget call passes no
`--orig-tag` (`.github/workflows/test-and-build-tag.yml:154`). The script's default is
`latest` (`scripts/dev/update_service_base_images.py`, `parse_args`), and `_update_line`
skips every line whose current tag is not that value. The templates have not carried
`latest` since `5e168b00`; they carry `dev-4314ac4`.

Measured on `origin/dev` at `5a26b5a3`, with each workflow's exact argv against a copy of
the real `src/cli/templates/dockerfiles/`:

- release argv (`--tag v2026.8.0 --switch-source ghcr`): **0 of 15 templates rewritten**,
  exit 0, no output.
- PR-preview argv (`--tag pr-7 --switch-source ghcr --orig-tag all`): **15 of 15 rewritten**.

The sibling call at `.github/workflows/pr-preview.yml:290` passes `--orig-tag all` and works.

Two consequences follow, and both are silent:

1. The smoke deployment builds the service images `FROM
   ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4` — the in-tree pin — not the versioned bases
   the release just pushed. The release is smoke-tested against a base it does not ship.
2. "Commit Dockerfile base image updates" (`.github/workflows/test-and-build-tag.yml:172`)
   reads `CHANGED_FILES` from an empty `git diff`, prints `No Dockerfile updates to commit`,
   and exits 0. `CLAUDE.md` and `docs/docs/proposals/release-plan-2026.md` both describe that
   step as a push of the base-image update to the dispatched ref. It has not pushed one since
   `5e168b00`.

Once the templates move to `@sha256:` digest pins (issue #333), the call stays a no-op for a
second, independent reason: a digest reference carries no tag, so no literal `--orig-tag`
value matches it. `--orig-tag all` covers both causes.

## What Changes

- The release retarget call gains `--orig-tag all`, which makes it match the PR-preview call.
  The `--orig-tag` **default stays `latest`**. A default that changes to suit one caller hides
  the next caller that depends on it, and the existing regression test
  `test_the_default_orig_tag_only_reaches_a_latest_pinned_line` pins that default.
- A new `--verify` mode on `scripts/dev/update_service_base_images.py` reads the references the
  templates declare and exits non-zero unless each one names the target reference. It shares
  the rewriter's `FROM` matcher and base-image map, so the check cannot disagree with the
  rewriter about what a base line is. It fails on three inputs: a reference on another tag or
  another registry, a reference to an `a2rchi` base the rewriter cannot place, and a run that
  matched no reference at all. The last two are the same failure in different clothes — a check
  that reads nothing passes without reading anything.
- A new step, "Verify the service templates point at this release's base images", runs
  directly after the retarget step and before the smoke deployment, and calls that mode with
  the tag and registry the retarget step just wrote.
- Two more calls guard the `release` job, which checks out the dispatched ref by name a second
  time and so does not provably hold the tree the smoke test proved. The first runs on that
  fresh checkout **before the job publishes anything** — `Promote base images to latest` moves
  the `latest` tag on ghcr and on docker.io, and `Commit version bump` pushes to the dispatched
  ref, and neither is undone by a later failure. The second runs immediately before the tag is
  created, over the tree those steps left behind. Neither makes the tagged tree provably
  identical to the smoke-tested one — that needs one resolved commit for every job, which the
  release mechanics trade away to push to the dispatched ref by name — but together they stop a
  tag being created over templates on the wrong base, and stop a doomed run from publishing
  half a release first.
- The commit step keeps its non-fatal empty-diff branch, and says why the tree is clean
  instead of only that it is.

## The decision this change had to make

Issue #339 asks whether the commit step must fail when it finds no diff, and recommends that
it must. This change verifies the postcondition instead, one step earlier. The reason is that
an empty diff has two causes, and only one of them is a defect:

- the retarget produced nothing, which is the defect this change repairs; or
- the templates already carry the release tag, which is what a **re-dispatch of the same tag**
  looks like after an earlier run of that same release pushed the commit. `CLAUDE.md` records
  that the workflow pushes to the dispatched ref, so this state is reachable, and an operator
  reaches it exactly when a first attempt failed late and must run again.

A hard failure on an empty diff turns that legitimate re-run into a release failure, after the
images are published and before the tag exists — the worst point in the run to stop. A check
of the reference itself separates the two cases: it fails in the first, and passes in the
second. It also fails before the smoke deployment spends its time, and it keeps working when
the templates move to digest pins, because it reads what the line says rather than whether the
line moved.

## Capabilities

### New Capabilities

- `service-base-images`: adds one requirement covering the release retarget and its
  verification. The capability directory does not exist under `openspec/specs/` yet. The change
  that introduces it (`openspec/changes/fix-issue-266-ghcr-base-images/`) merged at `5e168b00`
  and is not archived, and `openspec/changes/fix-issue-334-digest-pinned-base-refs/` adds to it
  as well. This change therefore adds a requirement rather than modifies one.

### Modified Capabilities

None.

## Impact

- `.github/workflows/test-and-build-tag.yml` — the retarget call, three new verification steps
  (before the smoke deployment, on the `release` job's checkout, and before the tag), and the
  commit step's empty-diff message. This is a release-run behavior change, in two ways. A
  release whose templates do not carry the release tag now fails at a verification step. And
  the retarget now produces a real diff, so the commit-and-push at the end of `smoke-test` runs
  for the first time since `5e168b00` — the dispatched ref has to permit that bot push, which
  `CLAUDE.md` already requires.
- `tests/unit/test_update_service_base_images.py` — tests for the `--verify` mode, and tests
  that read the release argv and the step order out of the workflow YAML rather than restating
  them, so a call site that drifts again turns the suite red. Plus a docstring correction on
  `test_the_default_orig_tag_only_reaches_a_latest_pinned_line`, which described the release
  call as one that passes no `--orig-tag`.
- `scripts/dev/update_service_base_images.py` — the `--verify` mode and its argument checks.
  The rewriting path is untouched, and the `--orig-tag` **default is unchanged**: the defect
  this change repairs is at the call site, and that is where it is repaired.
- `docs/docs/developer_guide.md` — documents `--verify` in the section that already documents
  the script's other flags, per `AGENTS.md`.
- `.github/workflows/pr-preview.yml` — **not** edited. Its call is already correct.
- The Dockerfile templates — **not** edited. They keep the `dev-4314ac4` pin, and the release
  run rewrites them in its own checkout.
- Coverage: `scripts/gate.sh:146` measures `--cov=src`, so neither the workflow nor the script
  reports lines to `diff-cover`. The named tests are the evidence, not the percentage.
