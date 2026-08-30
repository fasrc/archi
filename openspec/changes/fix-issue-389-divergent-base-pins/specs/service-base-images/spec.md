## ADDED Requirements

### Requirement: The deploy preflight refuses service templates that disagree about a required base image

The deploy preflight SHALL refuse a deployment when two service templates declare different references for the same required base image, and the refusal SHALL name every reference and the templates that declare it.

`base_reference` (`src/cli/managers/base_image_preflight.py:123`) returns the first reference
whose text names the requested image and stops, and both entry points call it once per
required image. Exactly one reference per base therefore reaches `run_preflight`, however many
templates declare one, and nothing checks that the rest say the same thing.

Measured on `origin/dev` at `7c9915d0`, with two service templates pinning
`a2rchi-python-base` at different digests, `required_base_images` returns the `aaaa…` digest
alone and `templates_missing_base_reference` returns `[]`. The `bbbb…` digest the second
template builds from is never named, never probed, and never version-checked. The preflight
reports the deployment ready, `--force` runs `remove_existing_deployment()`, and the build
fails on an image nobody established — the post-teardown failure this capability exists to
prevent.

This is the module's own invariant broken from inside: no path may pass silently on an
assumption, and reading one reference out of several is the assumption that the templates
agree. It cannot be met by reading a different single reference, because the preflight has no
basis for choosing among them. Only naming the disagreement is honest.

The refusal is not the repository guard `test_service_templates_pin_one_explicit_base_tag`
restated. That guard keys on the `# base-image-pin:` annotation, so two templates carrying the
same annotation above different digests satisfy it; and it is a repository test, which never
runs against the templates installed in the environment where the deployment is decided.

#### Scenario: Two templates pinning one base at different digests are refused

- **WHEN** a template directory holds two service templates on `ghcr.io/fasrc/a2rchi-python-base` at different digests, and `required_base_images` is called for a deployment that requires the python base
- **THEN** the call raises `BaseImagePreflightError`
- **AND** the message names both template files
- **AND** the message names both references

#### Scenario: The deploy entry point refuses before any image work

- **WHEN** `enforce_base_images` is called against that same directory
- **THEN** it raises `BaseImagePreflightError`
- **AND** the probe pulled nothing

The assertion that the probe pulled nothing is what holds the ordering contract. A refusal
that arrived after the first pull would still arrive after `remove_existing_deployment()`
(`cli_main.py:294`), which is the failure being prevented rather than a late report of it.

The check has to fire at both entry points, not at the one that is easier to test.
fasrc/archi#381 shipped a refusal that lived only in `required_base_images`, which has no
production caller, so the deploy path went on silently — the same fail-open, one function over.

#### Scenario: Templates that agree are decided exactly as before

- **WHEN** every service template in a directory names one reference per base image
- **THEN** `required_base_images` returns the same references, in the same order, as it did before this requirement existed
- **AND** this holds for a deployment requiring only the python base and for one requiring the pytorch base as well

A guard whose cost is a changed result on the healthy path is not a guard. The in-tree
templates are the healthy path: 15 service templates declaring 1 distinct reference for
`a2rchi-python-base` and 1 for `a2rchi-pytorch-base`, measured on this base.

#### Scenario: A single template naming one base twice is not a disagreement

- **WHEN** one service template names the same base image on two `FROM` lines and every other template agrees with that template
- **THEN** no divergence refusal is raised

Divergence is a disagreement between templates. One template naming a base twice is a
multistage build, whose shipping stage is decided elsewhere in this capability. Reading every
`FROM` match as a separate opinion would refuse a valid multistage template.

### Requirement: The agreement check covers the base images the deployment requires, and says so

The preflight SHALL check for divergent references only among the base images `required_base_image_names` returns for the deployment, and SHALL record that scope in the docstring of the check.

Both readings are defensible, which is why the choice is written down rather than left to be
inferred from the code. Checking a base nothing will build is a refusal the operator cannot act
on without editing a template unrelated to the deployment in front of them. Not checking it
leaves a split pin hidden until the next deployment that does need that base.

The scope decides the trade in favour of a refusal the operator can always act on, and the
docstring carries the cost so the next reader does not re-derive the argument, or reverse it by
accident.

#### Scenario: A split pin on a base this deployment does not need is not refused

- **WHEN** a template directory holds an agreeing python base across every template and two different references for the pytorch base, and the deployment requests no GPU and no grader
- **THEN** `required_base_images` returns the python reference without raising

#### Scenario: The same directory is refused once the deployment needs that base

- **WHEN** the same directory is used for a deployment that enables the grader
- **THEN** `required_base_images` raises `BaseImagePreflightError`
- **AND** the message names the pytorch base
