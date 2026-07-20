## ADDED Requirements

### Requirement: Dry runs do not require a container runtime

`archi create --dry` SHALL validate configuration, secrets, and service selection and
print its summary without requiring a container runtime on the host. Runtime-availability
preflight checks MUST NOT gate the `--dry` code path, because a dry run starts no
container.

#### Scenario: Dry run on a host with no Docker

- **WHEN** `archi create --dry` is invoked with a valid config and secrets file, `--podman`
  is not passed, and `check_docker_available()` returns `False`
- **THEN** the command exits 0 and prints the dry-run summary
- **AND** no "Docker is not available on this system" error is raised

#### Scenario: Dry run on a host with Docker

- **WHEN** `archi create --dry` is invoked with a valid config and secrets file and
  `check_docker_available()` returns `True`
- **THEN** the command exits 0 and prints the dry-run summary, unchanged from prior behavior

### Requirement: Real deployments still require a container runtime

A non-dry `archi create` SHALL continue to fail fast with a `ClickException` when
`--podman` was not passed and Docker is unavailable, before any deployment work is
performed. The error message MUST be unchanged, so existing operator guidance stays valid.

#### Scenario: Real deployment on a host with no Docker

- **WHEN** `archi create` is invoked without `--dry` and without `--podman`, and
  `check_docker_available()` returns `False`
- **THEN** the command exits non-zero with a `ClickException` whose message states that
  Docker is not available and suggests the `--podman` option

#### Scenario: Real deployment with --podman on a host with no Docker

- **WHEN** `archi create` is invoked without `--dry` but with `--podman`, and
  `check_docker_available()` returns `False`
- **THEN** the Docker-availability check is skipped and the command proceeds

#### Scenario: Forced re-create on a host with no Docker

- **WHEN** `archi create --force` is invoked without `--dry` and without `--podman` against
  an existing deployment, and `check_docker_available()` returns `False`
- **THEN** the command exits non-zero before `handle_existing_deployment()` runs
- **AND** the existing deployment directory is left intact, because a forced cleanup
  swallows a failed compose stop and would otherwise remove the deployment on a host that
  cannot bring it back up

#### Scenario: Real deployment on a host with no Docker at maximum verbosity

- **WHEN** `archi create` is invoked without `--dry` and without `--podman` at
  `--verbosity 4`, and `check_docker_available()` returns `False`
- **THEN** the command still exits non-zero
- **AND** the preflight is not swallowed by the broad exception handler that only prints a
  traceback at verbosity >= 4

### Requirement: Dry-run smoke coverage executes without a container runtime

The `archi create` dry-run smoke tests SHALL execute — not skip — in environments with
neither `docker` nor `podman` on PATH, so the runtime-independence of `--dry` is actually
verified by the gate rather than silently unverified.

#### Scenario: Gate run inside the runtime-less loop container

- **WHEN** the unit gate runs in an environment where neither `docker` nor `podman` is on
  PATH
- **THEN** the dry-run smoke tests in `tests/unit/test_cli_create_dev_smoke.py` run and pass
- **AND** only tests that genuinely require a container runtime remain skipped

#### Scenario: Smoke tests on a host that does have a container runtime

- **WHEN** the unit gate runs on a host with `docker` or `podman` installed
- **THEN** no test in `tests/unit/test_cli_create_dev_smoke.py` creates volumes, containers,
  or deployment files on the host runtime — non-dry runs are halted at the first step after
  the preflight
- **AND** each test resolves `ARCHI_DIR` to its own temporary directory, patching the
  module-level constant rather than only the environment variable, so tests cannot leak into
  one another or into the operator's real `~/.archi`
