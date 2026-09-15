## Why

`archi create --dry` validates configuration and prints a summary — it never launches a
container — yet it aborts with "Docker is not available on this system" on any machine
without a Docker binary. This makes the gate environmentally flaky: on PR #111 (head
`eeef7f7a`, a docs/shell-only diff) two gate runs on the *same commit* disagreed —
[run 29549066080](https://github.com/fasrc/archi/actions/runs/29549066080) failed on that
message while [run 29549067994](https://github.com/fasrc/archi/actions/runs/29549067994)
passed. The flake is a symptom; the real defect is a preflight check that gates a code
path with no runtime dependency. Fixes fasrc/archi#112.

## What Changes

- Move the Docker-availability check in the `create` command so it runs only on the
  **non-dry** path — after the `--dry` early return, before any container operation.
- `archi create --dry` exits 0 when no container runtime is present and `--podman` was not
  passed. Non-dry `create` without `--podman` still raises the same `ClickException` with
  the same message (no user-visible change to real deployments).
- Narrow the module-level `pytestmark` skip in
  `tests/unit/test_cli_create_dev_smoke.py`, which currently skips the whole file when
  neither `docker` nor `podman` is on PATH. That skip exists *because* of this bug; once
  dry runs no longer need a runtime, the dry-run smoke tests must actually execute in
  runtime-less environments (including the loop container) rather than silently skipping.
- Add a regression test that forces `check_docker_available()` to `False` and asserts both
  halves of the contract (dry exits 0; non-dry raises).

No change to `restart` (`src/cli/cli_main.py:436`) or `evaluate`
(`src/cli/cli_main.py:713`): verified those subcommands expose no `--dry` flag — the only
`--dry` option in the CLI is `create`'s, at `src/cli/cli_main.py:90`. Their checks are
correctly placed and stay put.

## Capabilities

### New Capabilities
- `cli-create-preflight`: which preflight checks `archi create` runs, and on which code
  paths — specifically that runtime-dependent checks gate only runtime-dependent work, so
  `--dry` validation succeeds without a container runtime.

### Modified Capabilities

(none — no existing spec in `openspec/specs/` covers CLI create preflight behavior)

## Impact

- `src/cli/cli_main.py` — the `create` command: one check relocated within the function.
- `tests/unit/test_cli_create_dev_smoke.py` — skip condition narrowed; one test added.
- CI: removes an environmental failure mode from `scripts/gate.sh` runs on Docker-less
  runners, and un-skips existing dry-run smoke coverage that was previously invisible.
- No dependency, API, config, or deployment changes. No user-facing behavior change for
  real (non-dry) deployments, so no `docs/` update is required.
