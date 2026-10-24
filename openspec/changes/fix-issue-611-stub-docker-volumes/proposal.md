## Why

`tests/unit/test_cli_create_dev_smoke.py::test_force_create_with_agent_config_inside_deployment_keeps_existing_deployment`
(line 2438, added in #590) calls the real docker CLI. It stubs `check_docker_available` but not
`VolumeManager.create_required_volumes`, and `cli_main.create` calls
`volume_manager.create_required_volumes(compose_config)` (`src/cli/cli_main.py:929`) before
`refuse_agent_config_inside_deployment(...)` (`src/cli/cli_main.py:931`). The volume check runs
`docker volume ls` through an un-mocked `subprocess.Popen`
(`src/cli/managers/volume_manager.py:96`, `src/cli/utils/command_runner.py:16`).

On a host with no `docker` binary the test fails with `Error: [Errno 2] No such file or directory: 'docker'`.
Measured on 2026-10-04 at `origin/dev` `be42428b` with `docker` removed from `PATH`: 1 failed,
42 passed in the module; across `tests/unit/` this is the only docker-caused failure. The
pre-commit gate runs all of `tests/unit/`, so the Ralph loop sandbox (no docker) cannot commit any
change. This halted the #445 run on 2026-10-03. A unit test also must not query the host's real
docker volumes.

## What Changes

- Add the `create_required_volumes` stub that the sibling tests use (lines 1031, 1117, 1508) to
  this one test, next to its `check_docker_available` stub.
- No production code changes.

## Capabilities

### New Capabilities
- `unit-test-docker-isolation`: the `archi create` smoke tests run without a docker binary and
  never query the host's docker volumes.

### Modified Capabilities
None.

## Impact

- `tests/unit/test_cli_create_dev_smoke.py` only (one test function).
- Unblocks the loop sandbox gate for every nightly run.
