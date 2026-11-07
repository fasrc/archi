## Why

`archi create --force` and `archi evaluate --force` tear the existing deployment down and
then run every stage of `TemplateManager.prepare_deployment_files()`. Any of those stages can
raise on deterministic config input, so a replacement that was always going to fail still
costs the operator the running deployment. Nine issues and PRs (#287, #292, #293, #299,
#306, #317, #325, #391, #436) closed one route each; the list is still growing. This change
closes the class for deterministic render failures (fasrc/archi#294).

The operator decided the approach on 2026-09-26 and confirmed it on 2026-09-27: **render the
whole plan once into a temporary directory, discard it, and only then tear down** (option 2).
A staging directory with a two-rename swap (option 1) and more per-route guards (option 3)
were rejected.

## What Changes

- Add `TemplateManager.preflight_render(plan, config_manager, secrets_manager, **options)`.
  It runs the same stage list as `prepare_deployment_files()` into a
  `tempfile.TemporaryDirectory`, skips the source copy and the live port probe, re-raises
  the first stage failure unchanged, and always removes the temporary directory.
- `create()` and `evaluate()` call it after every existing validation and before
  `remove_existing_deployment()`. A failure refuses the run and leaves the old deployment
  untouched.
- `create --dry` runs the preflight too, so a dry run is a full render check. It still
  creates nothing on the host.
- In `create()`, move volume creation (`VolumeManager.create_required_volumes(compose_config)`,
  without the config) above the teardown (never under `--dry`). Local-file staging
  (`VolumeManager.stage_local_files()`) stays below the teardown: it copies into the
  data-manager volume that the running deployment still mounts. `evaluate()` uses the same
  order: volumes above the teardown, staging below it.
- Update the comments in `src/cli/cli_main.py` and the note in `docs/docs/fasrc_archi.md`
  that point at #294 as open.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-create-preflight`: adds one requirement — the replacement deployment renders
  completely before any teardown, for both `create` and `evaluate`.

## Impact

- Code: `src/cli/managers/templates_manager.py` (new method, shared stage runner),
  `src/cli/cli_main.py` (call sites only, in `create()` and `evaluate()`).
- Tests: new tests in `tests/unit/test_render_preflight.py`; three existing tests in
  `tests/unit/test_cli_create_dev_smoke.py` move their stop point (design D6).
- Docs: `docs/docs/fasrc_archi.md` (the `--force` note).
- Out of scope: making `compose up` transactional, a staging swap, `deploy/**`, and any
  non-dry create against a real deployment.
