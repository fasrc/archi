## ADDED Requirements

### Requirement: The archi create smoke tests SHALL run without a docker binary

Every test in `tests/unit/test_cli_create_dev_smoke.py` MUST pass on a host where no `docker` binary is on `PATH`, and MUST NOT run a real docker command. A test that reaches the volume step of `cli_main.create` MUST stub `VolumeManager.create_required_volumes`.

#### Scenario: Inside-deployment agent config test with no docker

- **WHEN** `test_force_create_with_agent_config_inside_deployment_keeps_existing_deployment` runs with no `docker` on `PATH`
- **THEN** the test passes
- **AND** the existing deployment is not torn down and the command exits non-zero with the `services.chat_app.evaluations.agent_config_path` error

#### Scenario: Whole module with no docker

- **WHEN** `tests/unit/test_cli_create_dev_smoke.py` runs with no `docker` on `PATH`
- **THEN** no test fails with `No such file or directory: 'docker'`
