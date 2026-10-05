## Context

`evaluate()` (`src/cli/cli_main.py:921-933` on `origin/dev` 570814d0) creates the required
volumes before the teardown and the existence guard, by design: "a volume that cannot be
created refuses here, before the teardown". A unit test that drives `evaluate --force` past
that point therefore runs the real `VolumeManager.create_required_volumes`, which calls the
`docker` or `podman` binary. On the host the call succeeds, so the test passes. In the loop
container there is no binary, so the CLI exits with `[Errno 2] ... 'docker'` before it
reaches the guard that the test asserts on.

## Goals / Non-Goals

Goals:
- The test passes with no container runtime on `PATH`, and still proves the post-removal
  "already exists" guard.

Non-Goals:
- No change to the order of steps in `evaluate()`. That order is a deliberate contract
  (`cli-create-preflight`).
- No `docker` binary in the loop image. The image is control plane, and a unit test must
  not need it.

## Decisions

D1. Patch `VolumeManager.create_required_volumes` on the class with a recorder that
appends to a local list. This is the pattern the sibling tests use (lines 1031, 1117,
1497), so the file stays consistent.

D2. Assert the recorder was called exactly once. This keeps the test honest: it proves the
command reached the volume step and then the guard, so a later reorder that skips the
volume step becomes visible instead of silently passing.

D3. Sibling audit is complete: a full `tests/unit/` run with `PATH` reduced to `python3`
and `git` gave 5419 passed, 55 skipped, 1 xfailed with only this test deselected. No other
test needs the change.

## Risks / Trade-offs

- The red state appears only without a runtime on `PATH`. On the host the unpatched test
  is green. Reproduce the red with a reduced `PATH` (task 1.1), not with the host's default
  `PATH`.
