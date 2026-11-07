## Context

Issue #611. The smoke tests in `tests/unit/test_cli_create_dev_smoke.py` drive `cli_main.create`
through `CliRunner`. Tests whose run reaches the volume step stub
`VolumeManager.create_required_volumes` with
`monkeypatch.setattr(VolumeManager, "create_required_volumes", lambda self, *a, **kw: None)`
(lines 1031, 1117, 1508). The test at line 2438 reaches the volume step (the inside-deployment
refusal at `src/cli/cli_main.py:931` runs after `create_required_volumes` at line 929) but has no
stub, so it runs `docker volume ls` on the host.

## Goals / Non-Goals

**Goals:**
- The test passes on a machine with no `docker` binary on `PATH`.
- The test never runs a real docker command.

**Non-Goals:**
- No change to `cli_main.create` call order or any production code.
- No module-wide autouse docker guard. A measured run with no `docker` on `PATH` shows this is
  the only docker-dependent test in `tests/unit/`, so a broader guard is not needed for this fix.

## Decisions

- **D1: Copy the sibling stub verbatim.** Use the same `monkeypatch.setattr(VolumeManager, ...)`
  line as the sibling tests, placed next to the existing `check_docker_available` stub. If
  `VolumeManager` is not already in scope in that function, import it the same way the sibling
  tests do. This keeps one idiom in the file.
- **D2: Verify with docker removed from `PATH`.** On the dev host docker exists, so a normal run
  passes before and after the fix. Prove the fix with a `PATH` that has `python` but no `docker`.
  In the loop sandbox there is no docker, so the gate itself is the proof.

## Risks / Trade-offs

- The test still asserts the refusal behaviour, so the stub does not weaken it: the refusal runs
  after the volume step and does not depend on real volumes.
- Test-only diff: `diff-cover` scores only `src/`, so the 80% patch-coverage gate has no lines to
  score. This is expected.
