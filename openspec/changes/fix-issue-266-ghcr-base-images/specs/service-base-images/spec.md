## ADDED Requirements

### Requirement: Service templates reference fork-controlled, pinned base images

Every service Dockerfile template whose `FROM` names an `a2rchi-*-base` image SHALL reference the `ghcr.io/fasrc/` registry prefix at a pinned tag, and SHALL NOT reference `docker.io/a2rchi/` or the floating tag `latest`.

The fork does not control `docker.io/a2rchi/*`. Upstream can replace what `latest` serves at
any time, and did: the image it currently serves carries Python 3.10.20 against this
project's declared floor of `>=3.11` (`pyproject.toml:5`), which breaks `pip install .` in
every service build on a host that does not already hold a locally built base image.

Both properties are load-bearing and neither implies the other. A `ghcr.io/fasrc/` reference
still floats if its tag is `latest`; a pinned tag still belongs to upstream if its prefix is
`docker.io/a2rchi/`.

Scope is the 15 service templates. `Dockerfile-base`, `Dockerfile-base-gpu`,
`Dockerfile-postgres`, `Dockerfile-grafana`, and the two `base-*-image/Dockerfile` files are
excluded: they define the base images themselves or build on third-party images.

#### Scenario: Every service template names a pinned ghcr base

- **WHEN** the service Dockerfile templates under `src/cli/templates/dockerfiles/` are examined
- **THEN** each template whose `FROM` names an `a2rchi-*-base` image uses the `ghcr.io/fasrc/` prefix
- **AND** each such reference carries an explicit tag that is not `latest`
- **AND** all such references share the same tag

#### Scenario: A regression to the upstream registry fails the gate

- **WHEN** any service template is changed back to `docker.io/a2rchi/a2rchi-python-base`
- **THEN** the unit suite fails
- **AND** the failure message names the offending file

#### Scenario: A regression to a floating tag fails the gate

- **WHEN** any service template keeps the `ghcr.io/fasrc/` prefix but its tag is changed to `latest`
- **THEN** the unit suite fails
- **AND** the failure message names the offending file

This scenario is separate from the one above because a check that tests only the registry
prefix passes a floating `ghcr.io/fasrc/a2rchi-python-base:latest`, which reintroduces the
same class of defect from a different registry.

#### Scenario: Base and third-party templates are left alone

- **WHEN** the same examination reaches `Dockerfile-base`, `Dockerfile-base-gpu`, `Dockerfile-postgres`, `Dockerfile-grafana`, or either `base-*-image/Dockerfile`
- **THEN** those files are not required to carry a `ghcr.io/fasrc/` reference
- **AND** their existing `FROM` lines are unchanged
