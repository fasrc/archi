## ADDED Requirements

### Requirement: A service template counts as covered only when its base is one the preflight probes

The deploy preflight SHALL treat a service template as covered only when the base it builds on is a base the preflight will actually probe, and SHALL refuse and name any template whose base it will not probe.

The preflight probes exactly what `required_base_image_names` returns
(`src/cli/managers/base_image_preflight.py:168`) — the python base always, the pytorch base when
a GPU is requested or the grader is enabled. The coverage check accepts any name matching
`a2rchi-\w+-base` (`:43`). Those two answers must come from one declaration, because a template
whose base is accepted by the second and never probed by the first is covered on paper and
unbuilt in fact.

The failure this closes is not that the preflight returns nothing. `base_reference` (`:122`)
resolves each required name from the first template that names it, so the other healthy templates
still supply both references. The preflight returns a complete-looking answer covering one fewer
service than the build needs, and under `--force` the build failure lands after
`remove_existing_deployment()` (`src/cli/cli_main.py:294`) has already run.

The strictness belongs in the coverage check, not in the shared `_FROM_BASE_RE`. `base_reference`
uses that same pattern to find the pinned reference for a name it has already been given, so
narrowing the pattern would only stop it finding pins it should find.

#### Scenario: A template on an a2rchi base the preflight does not probe is refused

- **WHEN** a template in the declared service set builds on an `a2rchi-*-base` image whose name is not one the preflight probes
- **THEN** the deploy preflight refuses
- **AND** the refusal names that template

Measured on `origin/dev` at `7c9915d0`, a fixture of one digest-pinned `Dockerfile-chat` plus a
`Dockerfile-node` on `ghcr.io/fasrc/a2rchi-node-base@sha256:<64 hex>` reported
`missing: []` and `enforce: NO REFUSAL`. The refusal must come from `enforce_base_images`, the
function `archi create` calls; a refusal reachable only through `required_base_images` does not
satisfy this scenario, because that helper has no production caller (fasrc/archi#381).

#### Scenario: The probed set and the accepted set are one declaration

- **WHEN** the set of base names the preflight probes is read
- **AND** the set of base names the coverage check accepts is read
- **THEN** both read the same named constant

A second literal spelling of a base name is how the two answers drift apart. Naming the set once
means adding a third base image is a single edit that both the probe rule and the coverage check
see, rather than an edit that satisfies one and silently widens the other.

#### Scenario: A correctly based set of templates is unaffected

- **WHEN** every template in the declared service set builds on a base the preflight probes
- **THEN** the preflight returns the references it returned before this change
- **AND** the two-image rule of design D4 is unchanged

This is a new refusal path, not a new answer. The 15 real service templates all name the python or
pytorch base, so a correct tree must compute exactly what it computed before.

### Requirement: A multistage template is judged by the stage the deployment runs

The coverage check SHALL determine a template's base from its final stage rather than from the first base reference in the file, and SHALL follow a final stage that names an earlier stage back to that stage's base.

A multistage template's earlier stages are build scaffolding. The image the deployment runs is the
final stage, so it is the only stage whose base the preflight needs to be able to fetch. Reading
the first match anywhere in the file answers a question nobody asked.

#### Scenario: A multistage template whose final stage leaves the a2rchi base is refused

- **WHEN** a template's early stage builds on an a2rchi base the preflight probes
- **AND** its final stage builds on a third-party image
- **THEN** the deploy preflight refuses
- **AND** the refusal names that template

Measured on `origin/dev` at `7c9915d0`: a fixture whose `Dockerfile-multi` held
`FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<64 hex> AS builder` followed by
`FROM docker.io/library/debian:12` reported `missing: []` and `enforce: NO REFUSAL`.

#### Scenario: A multistage template whose final stage returns to an a2rchi stage stays covered

- **WHEN** a template's final stage names an earlier stage that builds on a base the preflight probes
- **THEN** the template is covered
- **AND** the deploy preflight does not refuse it

A check made stricter has to be tested for becoming strict about the wrong thing. Copying build
output back onto the a2rchi base is the ordinary reason to write a multistage service template,
and refusing it would make the guard an obstacle rather than a contract.

#### Scenario: A base reference the check cannot resolve is refused, not assumed

- **WHEN** a template's final stage names a reference the check cannot resolve to a base name
- **THEN** the template is reported
- **AND** the deploy preflight refuses

`base_image_preflight.py:15` states the governing invariant: every path either establishes that a
base image is usable, refuses, or says out loud that it could not tell, and no path may pass
silently on an assumption. An unresolvable reference — an `ARG`-substituted `FROM ${BASE_IMAGE}`,
or a form the pattern does not match — is that assumption. The check falls to the refusing side,
and the code states which forms it resolves rather than implying it resolves all of them.
