## ADDED Requirements

### Requirement: The preflight establishes the third-party base images of the planned services
The base-image preflight SHALL establish the availability of each third-party image that a planned service's template builds FROM, before any destructive action, and SHALL NOT apply the Python-floor check to such an image.

The third-party templates are `Dockerfile-postgres` (service `postgres`) and
`Dockerfile-grafana` (service `grafana`). The reference is the template's final-stage
`FROM`. An image is in scope only when its service is enabled in the compose plan. Postgres
is enabled whenever it is resolved as a dependency, and on every `evaluate` plan. Grafana
is enabled only when the grafana service is enabled.

The two-image rule for the `a2rchi-*-base` images does not change. A plain string
reference passed to `run_preflight` still receives the Python-floor check.

#### Scenario: Postgres base image cannot be obtained
- **WHEN** `enforce_base_images` runs for a compose plan that enables `postgres`, and the
  probe reports `docker.io/pgvector/pgvector:pg17` as absent and its pull as refused
- **THEN** `enforce_base_images` raises `BaseImagePreflightError` that names that image
- **AND** the probe never reads a Python version for that image

#### Scenario: Grafana is probed only when it is planned
- **WHEN** `enforce_base_images` runs for a compose plan in which `grafana` is not enabled
- **THEN** the probe receives no call for `docker.io/grafana/grafana-enterprise:10.2.0`
- **WHEN** the plan enables `grafana`
- **THEN** that image is probed for availability, with no Python-floor check

#### Scenario: A plain string reference keeps the floor check
- **WHEN** `run_preflight` receives a plain string reference to a present image
- **THEN** it reads that image's Python version and compares it against the floor

#### Scenario: Dry run with no container runtime
- **WHEN** a dry run has no usable container runtime and the plan enables `postgres`
- **THEN** the pgvector image is reported as NOT VERIFIED with the no-runtime cause
- **AND** its message contains no Python-floor or Python-version wording

#### Scenario: Dry run with a reachable third-party image
- **WHEN** a dry run finds the pgvector image absent locally but reachable in its registry
- **THEN** the outcome for that image is AVAILABLE, because no version needs to be read

#### Scenario: A third-party template has no readable FROM
- **WHEN** a planned service's third-party template is missing or has no `FROM` line
- **THEN** `enforce_base_images` raises `BaseImagePreflightError` that names the service
  and the template, and does not continue as though there were nothing to check
