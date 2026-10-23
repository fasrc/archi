## Why

`tests/unit/test_cli_create_dev_smoke.py::test_force_evaluate_refuses_when_removal_silently_fails`
fails in any environment that has no `docker` or `podman` binary on `PATH`
(fasrc/archi#594). The Ralph loop container is such an environment, so
`bash scripts/gate.sh` is red on `dev` inside the loop and no loop turn can commit.
Every unattended drain halts on its first commit until this is fixed (#563 and #564
both halted on it on 2026-10-01).

Reproduced on `origin/dev` 570814d0 on 2026-10-02 with `PATH` reduced to `python3` and
`git`: the file gives 1 failed, 40 passed, with
`[Errno 2] No such file or directory: 'docker'`. The full `tests/unit/` suite under the
same `PATH` gives 5419 passed with this one test deselected, so this test is the only one
that depends on a real container runtime.

Root cause: `evaluate()` in `src/cli/cli_main.py` calls
`volume_manager.create_required_volumes(compose_config)` (line 925) before
`remove_existing_deployment(...)` (line 927) and the "already exists" guard (line 931).
`create_required_volumes` shells out to the real runtime. The test patches
`check_docker_available` but not the volume call. Its siblings in the same file already
patch it (`VolumeManager.create_required_volumes` at lines 1031, 1117 and 1497).

## What Changes

- In `test_force_evaluate_refuses_when_removal_silently_fails`, patch
  `VolumeManager.create_required_volumes` with a recorder, the same way the sibling tests
  do, and assert that the evaluate path reached the volume step once before the guard.
- No change to `src/` behaviour. `src/cli/cli_main.py` stays as it is.

## Capabilities

### New Capabilities
- `unit-test-runtime-isolation`: the CLI smoke unit tests do not depend on a container
  runtime binary on `PATH`.

### Modified Capabilities
<!-- None. -->

## Impact

- `tests/unit/test_cli_create_dev_smoke.py` only (one test).
- Unblocks the gate inside the loop container, so every later nightly drain can commit.
- Test-only diff: `diff-cover` scores `src/` only, so the 80% patch gate scores no lines.
