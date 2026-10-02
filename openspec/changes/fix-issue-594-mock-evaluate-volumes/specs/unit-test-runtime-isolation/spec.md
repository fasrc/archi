## ADDED Requirements

### Requirement: CLI smoke unit tests SHALL NOT need a container runtime binary
The tests in `tests/unit/test_cli_create_dev_smoke.py` SHALL pass when neither `docker`
nor `podman` is on `PATH`. A test that drives `archi create` or `archi evaluate` past the
volume step MUST replace `VolumeManager.create_required_volumes` with a test double, so the
result does not depend on the host that runs the gate.

#### Scenario: evaluate --force refuses a runtime that survived removal, with no runtime on PATH
- **WHEN** `test_force_evaluate_refuses_when_removal_silently_fails` runs with `PATH`
  holding no `docker` or `podman` binary
- **THEN** the test passes, the CLI output contains "already exists", and
  `delete_deployment` was attempted exactly once

#### Scenario: the evaluate path still reaches the volume step
- **WHEN** the same test runs
- **THEN** the patched `VolumeManager.create_required_volumes` was called exactly once
  before the refusal

#### Scenario: the whole smoke file is runtime-free
- **WHEN** `python -m pytest tests/unit/test_cli_create_dev_smoke.py -q` runs with `PATH`
  holding no `docker` or `podman` binary
- **THEN** every test in the file passes
