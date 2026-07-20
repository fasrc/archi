## Context

`create` in `src/cli/cli_main.py` runs its Docker-availability preflight at line 141,
immediately after `warn_if_template_mismatch()` and well before the `--dry` early return
(~line 214, inside the `try:` block). Everything between those two points is
runtime-independent:

- `validate_services_selection(services)` — pure validation.
- `handle_existing_deployment(base_dir, name, force, dry, podman)`
  (`src/cli/utils/helpers.py:299`) — verified runtime-safe under `dry=True`: the branch
  that constructs a `DeploymentManager` and deletes containers is guarded by
  `if not dry:`; the dry branch only logs `[DRY RUN] Would remove existing deployment`.
- Manager initialization and compose-config assembly — file/template work only.

So the check can move to just after the `if dry:` early return without any container
operation preceding it. This is a relocation, not a redesign.

A second layer hides the bug: `tests/unit/test_cli_create_dev_smoke.py` carries a
module-level `pytestmark = pytest.mark.skipif(...)` that skips the entire file when
neither `docker` nor `podman` is on PATH — its comment explicitly cites this bug as the
reason. The unit gate runs inside the loop container (no nested runtime), so today these
tests never run there. Fixing the CLI without touching the skip would leave the new
regression test dormant in exactly the environment the fix targets.

## Goals / Non-Goals

**Goals:**
- `archi create --dry` exits 0 with no container runtime present and no `--podman`.
- Non-dry `create` fails fast with the identical `ClickException` message.
- The regression test actually executes in runtime-less environments (loop container, CI).
- Remove the environmental flake from `scripts/gate.sh`.

**Non-Goals:**
- Changing `restart` (`cli_main.py:436`) or `evaluate` (`cli_main.py:713`). Verified: the
  only `--dry` option in the CLI is `create`'s (`cli_main.py:90`), so those checks gate
  work that genuinely needs a runtime and are correctly placed.
- Changing what `check_docker_available()` itself detects, or the Podman-emulation
  heuristic in `src/cli/utils/helpers.py:28`.
- Changing the error message, the `--podman` contract, or any dry-run summary output.

## Decisions

**1. Relocate the check rather than add a `dry` condition in place.**
Rewriting line 141 as `if not dry and not podman and not check_docker_available()` would
also work and is a smaller diff, but it leaves a runtime precondition sitting visually
above ~70 lines of runtime-independent work, inviting the same mistake next time. Moving
it directly below the `if dry:` early return makes the invariant structural: everything
above the check is runtime-free by construction, everything below may touch a runtime.
Placement is immediately after the dry early-return block and before the first real
deployment operation.

**2. Narrow the test skip to the tests that truly need a runtime, rather than deleting it.**
The file may later grow non-dry tests that do need Docker. Preferred approach: drop the
module-level `pytestmark` and, if any test in the file actually launches containers, apply
a per-test `skipif` there instead. If every test in the file is a dry run (verify during
implementation), remove the `pytestmark` outright and delete the now-false comment. Do not
leave a blanket skip that would mask a regression of this very fix.

**3. Monkeypatch the name as imported into `cli_main`.**
`cli_main.py` pulls the helper in via `from src.cli.utils.helpers import *` (line 20), so
the call site resolves `src.cli.cli_main.check_docker_available`. The test must patch that
attribute — patching `src.cli.utils.helpers.check_docker_available` would not affect the
already-bound module global.

**4. Cover both halves of the contract in one test module.**
The dry-exits-0 case alone would pass even if the check were deleted entirely. A companion
assertion that non-dry still raises is what pins the behavior; it also supplies diff
coverage on the moved lines, which the ≥80% diff-coverage gate needs.

## Risks / Trade-offs

- **Un-skipping the smoke tests surfaces unrelated pre-existing failures in the loop
  container** (they have effectively never run there) → run the file locally in a
  runtime-less shell before committing; if a failure is unrelated to this change, keep it
  out of scope, restore a narrow per-test skip with a precise reason, and note it in the
  PR rather than silently widening the fix.
- **A real deployment fails later than before** — the check now runs a few dozen lines
  further down, after config validation and possible `--force` cleanup logging → cleanup
  work under `--force` is still guarded by `if not dry:` and the check precedes every
  container operation, so an operator without Docker still gets the same message before
  anything is launched. The only change is that they also get config-validation errors
  first, which is strictly more useful.
- **`--podman` regression risk** → the `--podman` short-circuit is carried verbatim with
  the moved condition and is covered by an explicit scenario in the spec.

## Migration Plan

None — no data, config, API, or deployment surface changes. Rollback is reverting the
commit.

## Open Questions

None. Placement, skip handling, and the patch target were each resolved against the tree.
