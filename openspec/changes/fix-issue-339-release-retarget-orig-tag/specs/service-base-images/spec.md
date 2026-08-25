## ADDED Requirements

### Requirement: The release run retargets the service templates and proves it did

The release workflow SHALL rewrite every service template's base reference to the base image
tag that the release built, regardless of the tag the template carried before, and SHALL fail
the run when any service template's base reference does not carry that tag after the rewrite.

The rewrite and the proof are one requirement because each one alone is worth little. A
rewrite with no proof is what this change repairs: the call at
`.github/workflows/test-and-build-tag.yml:154` passed no `--orig-tag`, took the script's
`latest` default, matched none of the 15 templates — which carry `dev-4314ac4` since
`5e168b00` — and exited 0 with no output. Measured against a copy of the real templates, the
release argv rewrote 0 of 15 and the PR-preview argv rewrote 15 of 15. A proof with no rewrite
is a red build on every release.

Silence is the property that made the defect survive a release cycle. Nothing downstream can
catch it: the templates the smoke test reads are well-formed, and they name a real image. The
smoke deployment passes, and it certifies a base image the release does not ship.

The rewrite matches on any current reference, not on one literal tag. A release must not
depend on what the templates happened to carry when it started. That also survives the move to
`@sha256:` digest pins (issue #333): a digest reference carries no tag, so no literal
`--orig-tag` value matches it, and a call narrowed to one tag becomes a second, independent
no-op.

The proof reads the reference on the line. It does not read whether the line changed. Those
two tests disagree on exactly one input — a template that already carries the release tag —
and there the reference is correct and the diff is empty.

#### Scenario: The release run rewrites a template pinned to an earlier build

- **WHEN** the service templates carry `ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4` and the release run retargets them at tag `v2026.8.0`
- **THEN** each service template reads `FROM ghcr.io/fasrc/a2rchi-python-base:v2026.8.0`
- **AND** the smoke deployment builds against that reference

#### Scenario: A template left on another tag fails the release run

- **WHEN** the retarget step leaves any service template's base reference on a tag other than the release tag
- **THEN** the release run fails at the verification step
- **AND** the failure names each template whose reference is wrong
- **AND** the smoke deployment does not run

The run fails before the smoke deployment, not after it. A smoke test against the wrong base
proves nothing, and the operator learns more from a named template than from a green run.

#### Scenario: A verification that finds no base reference fails the release run

- **WHEN** the verification step finds no service template that names an `a2rchi-*-base` image
- **THEN** the release run fails

A check that examines nothing passes. This scenario is separate because a wrong path, a
renamed directory, or a renamed base image all produce an empty set, and an empty set satisfies
"every reference carries the release tag" without reading a single file.

#### Scenario: A re-dispatch of the same release tag passes with nothing to commit

- **WHEN** a release run retargets templates that already carry the release tag
- **THEN** the verification step passes
- **AND** the commit step reports that the templates already carry the release tag
- **AND** the run does not fail

An operator reaches this state by dispatching a tag whose earlier run already pushed the
Dockerfile commit to the dispatched ref, which is what a re-run after a late failure looks
like. The reference is right, so the run is right.

#### Scenario: The rewriter's own default is unchanged

- **WHEN** `scripts/dev/update_service_base_images.py` runs with no `--orig-tag`
- **THEN** it matches only a reference tagged `latest`

The defect is at the call site, and the fix stays there. Changing the script's default to suit
one caller repairs that caller and hides the next one that relies on the default.
