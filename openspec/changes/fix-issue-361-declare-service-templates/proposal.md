# Declare which Dockerfile templates are service templates

## Why

Four guards each answer "which templates are service templates?" by the same derivation —
"the ones whose `FROM` line matches an `a2rchi-*-base` image" — and each one `continue`s past
a template it cannot match. Nothing in the repository holds the set itself, so a template
that stops matching stops being checked by all four at once.

Measured on `origin/dev` at `c60e6a69`, against the real
`src/cli/templates/dockerfiles/`:

- `Dockerfile*` templates: **19**
- templates naming an `a2rchi-*-base`: **15**
- templates naming none: **4** — `Dockerfile-base`, `Dockerfile-base-gpu`,
  `Dockerfile-postgres`, `Dockerfile-grafana`

Those 4 are correct as they are. Two define the base images themselves
(`FROM docker.io/library/python:3.11`, `FROM docker.io/pytorch/pytorch:2.0.1-cuda11.7-cudnn8-devel`)
and two build on third-party images (`docker.io/pgvector/pgvector:pg17`,
`docker.io/grafana/grafana-enterprise:10.2.0`). That is precisely why no guard can require
every `Dockerfile*` to name an a2rchi base, and why a stricter regex cannot replace a
declared set.

### What the existing guards do and do not catch

Issue #361 credits `test_service_templates_pin_one_explicit_base_tag`
(`tests/unit/test_python_version_declaration.py:387`) with asserting only that the collected
pin set is non-empty. That is accurate about that test, and it understates the tree: a
**second** guard already counts. `test_all_templates_share_one_pin_state`
(`tests/unit/test_base_image_preflight.py:136`) asserts
`len(_base_references()) == TEMPLATE_COUNT` against `TEMPLATE_COUNT = 15`
(`tests/unit/test_base_image_preflight.py:23`). So an **existing** service template that
drops its `a2rchi-*-base` line does turn the suite red today — the count falls to 14.

The hole that is open is the one the issue's own recommendation is aimed at: a **newly added**
service template. Measured — a template `src/cli/templates/dockerfiles/Dockerfile-probe`
containing `FROM docker.io/library/python:3.11` and `RUN pip install .`, added to the real
template directory on `c60e6a69`:

```
python -m pytest tests/unit/test_base_image_preflight.py \
  tests/unit/test_python_version_declaration.py -q
=> 101 passed
```

Every guard passes. The count still reads 15, because the 15 existing references are
untouched and the new template contributes none. `test_two_image_rule_still_matches_every_template`
(`tests/unit/test_base_image_preflight.py:262`) `continue`s at the no-match case (`:275`).
The new service ships on an unpinned third-party base, and the deploy preflight fetches no
base for it.

Three defects follow, and this change addresses all three:

1. **An added service template is silently outside the set.** This is the measured hole
   above, and it is the failure mode that matters: adding a service is a routine change,
   and nothing tells its author that the base-image contract exists.
2. **`TEMPLATE_COUNT = 15` is a magic number, not a declaration.** It cannot say *which*
   template fell out — the failure prints the whole reference list — and it lives in one
   test file. The other three guards never see it. The number 15 also appears in prose at
   `tests/unit/test_python_version_declaration.py:285` and
   `tests/unit/test_base_image_preflight.py:137`, so the same fact is recorded in three
   places and enforced in one.
3. **The deploy preflight cannot tell that a service is uncovered.** `required_base_images`
   (`src/cli/managers/base_image_preflight.py:92`) resolves each of the two known base
   images through `base_reference` (`:77`), which returns the first matching reference found
   in *any* template. A service template on a third-party base therefore does not make the
   preflight return nothing — the other 14 templates still supply both references. It makes
   the preflight return a complete-looking answer that covers one fewer service than the
   build will need. `base_image_preflight.py:15` states the module's governing invariant —
   "Every path either **establishes** that a base image is usable, **refuses**, or **says
   out loud that it could not tell**. No path may pass silently on an assumption." An
   uncovered service template is exactly such an assumption.

Found during the pre-PR adversarial review of #339 (round 1, `[high]`). #339 closed the
renamed-base half inside `verify_base_tags`
(`scripts/dev/update_service_base_images.py:297`, refusal at `:353`) and bounded its own
claim rather than widening scope — see the "A base image the rewriter cannot place fails the
release run" scenario in
`openspec/changes/fix-issue-339-release-retarget-orig-tag/specs/service-base-images/spec.md`,
which names this gap and says where it belongs. It belongs in the in-tree gate, not the
release run: a template with no base reference is a state of the repository, and the release
only ever checks out a ref the gate already passed.

## What Changes

- **A declared set, derived-with-exclusions**, in `src/cli/managers/base_image_preflight.py`
  beside `PYTHON_BASE`/`PYTORCH_BASE` and the design-D4 rule that already reads the
  templates. Every `Dockerfile*` under the template directory is a service template **unless
  it is named in an explicit exclusion list**. A new template is a service template by
  default and must be excluded deliberately.
- **The exclusion list carries its reason per entry**, so a later reader can tell a
  base-defining template from a third-party-based one without opening the file. Two of the
  four are the base images themselves; two build on third-party images.
- **A guard that the exclusion list stays honest**: a name in it that no longer exists on
  disk fails the suite. Without that, the list rots into a set of names that excludes
  nothing, and the set silently widens to include templates nobody re-examined.
- **The two existing guards read the declaration** instead of re-deriving it.
  `test_service_templates_pin_one_explicit_base_tag` gains the requirement that *every*
  template in the set contributes at least one pin — not merely that the collected set is
  non-empty. `test_all_templates_share_one_pin_state` derives its expected count from the
  declared set rather than from `TEMPLATE_COUNT = 15`. Neither is weakened; both keep every
  assertion they have.
- **Failures name the file.** The new assertions report the offending template path, which
  is what turns a red suite into a five-second diagnosis. The existing count assertion
  prints the whole reference list and cannot say which template is missing.
- **`required_base_images` refuses an uncovered service template.** It gains a check that
  every template in the declared set declares a base reference the preflight can place, and
  reports the ones that do not. Per the module invariant, the deploy path must not pass
  silently on the assumption that a service is covered.

## The decision this change had to make

Issue #361 asks where the declared set lives, and offers two candidates: a literal list of
the 15 service templates, or a derived rule with an explicit exclusion list. It recommends
the second. This change takes that recommendation, and the reason is worth recording because
the two options fail in opposite directions:

- A **literal list of 15** makes a new service template invisible until someone remembers to
  add it. The omission is silent, and it is the exact failure this change exists to end —
  the new list would need its own guard against being incomplete, which is the problem
  restated one level up.
- A **derived set with 4 exclusions** makes a new service template a member on the day it is
  added. If it names no `a2rchi-*-base`, the suite goes red and names it. The author either
  pins a base or adds a deliberate exclusion with a reason. Both outcomes are a decision
  someone made on purpose.

The cost of the second option is that a genuinely non-service template — a fifth
third-party-based helper — arrives red and must be excluded before it can merge. That is the
correct trade: a false red is a conversation, and a false green ships an unpinned base into a
deployment. The exclusion-list-honesty guard is what keeps that cost bounded, by making a
stale exclusion fail rather than quietly widen the set.

The set lives in `src/cli/managers/base_image_preflight.py` rather than in a test helper or a
data file because the deploy preflight is a runtime consumer, not only a test one. A test
helper could not be read by `required_base_images`, and a data file would add a parser and a
schema for four strings. `scripts/gate.sh:146` measures `--cov=src`, so this placement also
means the declaration and its consumers are coverage-measured, and the diff-cover floor
applies to them.

`scripts/dev/update_service_base_images.py` is deliberately **not** wired to the shared set.
It is a standalone script that the release workflow runs, and importing `src` into it would
add a path-manipulation coupling for no new guarantee: #339 already made `verify_base_tags`
refuse an `a2rchi` base it cannot place (`:353`), and the third-party case it cannot see is a
state of the repository that this change's in-tree guard now rejects before any release run
can check the tree out. The bound is stated here so the next reader does not read the
omission as an oversight.

## Capabilities

### New Capabilities

- `service-base-images`: adds two requirements — one for the declared set and the guards that
  read it, one for the deploy preflight's refusal. The capability directory does not exist
  under `openspec/specs/` yet. Four unarchived changes contribute to it
  (`fix-issue-266-ghcr-base-images`, `fix-issue-334-digest-pinned-base-refs`,
  `fix-issue-335-pin-service-dockerfiles-to-digests`,
  `fix-issue-339-release-retarget-orig-tag`), so this change adds requirements rather than
  modifies them.

### Modified Capabilities

None.

## Impact

- `src/cli/managers/base_image_preflight.py` — the declared set, the exclusion list with
  per-entry reasons, an accessor for the service templates, and the uncovered-template
  refusal in `required_base_images`. Coverage-measured; the new lines need tests, which the
  guards below supply.
- `tests/unit/test_base_image_preflight.py` — the expected count derives from the declared
  set instead of `TEMPLATE_COUNT = 15`; new tests for the added-template hole (against a
  fixture directory, not the real templates), for the exclusion-honesty guard, and for the
  `required_base_images` refusal. `test_two_image_rule_still_matches_every_template` and
  `test_all_templates_share_one_pin_state` keep every assertion they have.
- `tests/unit/test_python_version_declaration.py` — `test_service_templates_pin_one_explicit_base_tag`
  gains the every-member-contributes-a-pin assertion and reads the declared set. Its existing
  unpinned/unnamed assertions are untouched. The stale prose count at `:285` is corrected to
  point at the declaration rather than restate a number.
- The Dockerfile templates — **not** edited. The 19 / 15 / 4 split is already correct; this
  change declares it rather than changes it.
- `.github/workflows/**` — **not** edited, per the issue's constraint. The release-time check
  inherits whatever the in-tree gate guarantees.
- `scripts/dev/update_service_base_images.py` — **not** edited. See the decision section.
- A RED step must fail against a **fixture** template directory, not against the real
  templates. Making the real tree red to watch a test fail would leave the gate unable to
  commit, and the fixture proves the same discrimination.
