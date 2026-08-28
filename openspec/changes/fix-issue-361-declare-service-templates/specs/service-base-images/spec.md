## ADDED Requirements

### Requirement: The repository declares which templates are service templates

The repository SHALL hold one declaration of which Dockerfile templates are service templates, and every guard that needs that set SHALL read the declaration rather than re-derive it from what the templates happen to name.

The set is declared as a derivation with an explicit exclusion list: every `Dockerfile*` under
the template directory is a service template unless it is named in the exclusion list. A
template added to the repository is therefore a service template on the day it is added, and
leaving the set is a deliberate act with a recorded reason.

The direction of that default is the whole requirement. Four guards previously each derived
the set as "the templates whose `FROM` line names an `a2rchi-*-base` image", and each
`continue`d past a template it could not match — `scripts/dev/update_service_base_images.py`
at `update_base_tags` (`:375`) and `verify_base_tags` (`:327`),
`src/cli/managers/base_image_preflight.py` at `_FROM_BASE_RE` (`:33`), and
`tests/unit/test_base_image_preflight.py` at `:275`. A template that stopped matching stopped
being checked by all of them at once, and no single guard could tell the difference between
"this template is not a service" and "this service lost its base pin".

An added template is the case that survived the guards that exist. Measured on `origin/dev`
at `c60e6a69`: a template containing `FROM docker.io/library/python:3.11` and
`RUN pip install .`, added to the real template directory, left
`tests/unit/test_base_image_preflight.py` and
`tests/unit/test_python_version_declaration.py` at **101 passed**. The count assertion at
`tests/unit/test_base_image_preflight.py:145` still read 15, because the 15 existing
references were untouched and the new template contributed none.

A count is not a declaration. `TEMPLATE_COUNT = 15` catches an existing template that drops
its base line, and that is worth keeping, but it cannot name the template that fell out and
it cannot see one that was added. The declaration replaces the number as the source of the
expected count, so the two facts cannot drift apart.

#### Scenario: A new service template naming no a2rchi base fails the suite

- **WHEN** a template is added under the template directory, is not named in the exclusion list, and its `FROM` line names no `a2rchi-*-base` image
- **THEN** the unit suite fails
- **AND** the failure names that template's path

This is the measured hole. The author of a new service learns that the base-image contract
exists at the moment the contract is broken, and the two ways forward — pin a base, or add a
deliberate exclusion — are both decisions someone makes on purpose.

#### Scenario: An existing service template that loses its base pin fails and is named

- **WHEN** a template in the declared set has its `a2rchi-*-base` `FROM` line removed
- **THEN** the unit suite fails
- **AND** the failure names that template's path

The suite already failed on this input, by way of the count at
`tests/unit/test_base_image_preflight.py:145` falling from 15 to 14. What it did not do was
say which template. A reference list is not a diagnosis.

#### Scenario: A stale exclusion fails rather than widening the set

- **WHEN** the exclusion list names a file that does not exist under the template directory
- **THEN** the unit suite fails
- **AND** the failure names the missing entry

Without this, a renamed or deleted template turns its exclusion into a name that excludes
nothing. The set then widens by one, silently, and the widening is invisible because the
guard that would notice is the same one the stale entry defeats.

#### Scenario: The four excluded templates stay green

- **WHEN** the suite runs against the real template directory
- **THEN** `Dockerfile-base`, `Dockerfile-base-gpu`, `Dockerfile-postgres`, and `Dockerfile-grafana` are excluded from the set
- **AND** the declared set has 15 members out of 19 templates

Two of the four define the base images themselves and two build on third-party images
(`docker.io/pgvector/pgvector:pg17`, `docker.io/grafana/grafana-enterprise:10.2.0`). They have
no `a2rchi-*-base` reference to check, and requiring one of them would be wrong rather than
strict. This scenario pins the measured 19 / 15 / 4 split so a change to it has to be
deliberate.

#### Scenario: Every member of the set contributes a base pin

- **WHEN** the pin guard collects the `a2rchi-*-base` pins declared under the template directory
- **THEN** it requires a pin from every member of the declared set
- **AND** a non-empty collection is not sufficient to pass

`test_service_templates_pin_one_explicit_base_tag` asserted that the collected pin set was
non-empty (`tests/unit/test_python_version_declaration.py:395`) and internally consistent.
Fifteen correct templates satisfied that while a sixteenth shipped an unpinned third-party
base. The existing unpinned and unnamed assertions keep working unchanged; this adds the
membership check they had no way to make.

### Requirement: The deploy preflight refuses a service template it cannot cover

The deploy preflight SHALL refuse when a template in the declared service set declares no base image reference the preflight can place, and SHALL name each such template.

`base_image_preflight.py:15` states the invariant this serves: every path either establishes
that a base image is usable, refuses, or says out loud that it could not tell, and no path may
pass silently on an assumption. A service template the preflight cannot place is such an
assumption — the deploy proceeds as though that service's base were checked.

The precise failure is worth stating, because it is not that the preflight returns nothing.
`required_base_images` (`:92`) resolves each of the two known base images through
`base_reference` (`:77`), which returns the first matching reference found in **any**
template. With one service template moved onto a third-party base, the other 14 still supply
both references. The preflight therefore returns a complete-looking answer that covers one
fewer service than the build will need, and under `--force` the build failure lands after
`remove_existing_deployment()` has already run — the ordering this module exists to prevent.

#### Scenario: A service template on an unplaceable base refuses the preflight

- **WHEN** a template in the declared service set declares no base reference the preflight can place
- **AND** the preflight computes the base images this deployment requires
- **THEN** it refuses
- **AND** the refusal names that template

#### Scenario: A fully covered set of templates is unaffected

- **WHEN** every template in the declared service set declares a base reference the preflight can place
- **THEN** the preflight returns the same references it returned before this change
- **AND** the two-image rule of design D4 is unchanged

The refusal is a new failure path, not a new answer. A correct tree must compute exactly what
it computed before, or this change has altered the deploy behavior it was meant to guard.
