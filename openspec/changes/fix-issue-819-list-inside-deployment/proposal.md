## Why

A configured `input_lists` path that lives inside the deployment directory passes the #617 check and is then deleted by the `--force` teardown (issue #819). On `origin/dev` `f457d3d1`:

- `src/cli/cli_main.py:192` (`create`) and `:859` (`evaluate`): `refuse_missing_input_lists(config_manager.get_configs())` passes, because the file exists when the command starts.
- `src/cli/cli_main.py:320` (`create`) and `:950` (`evaluate`): `remove_existing_deployment(...)` (`src/cli/utils/helpers.py:316`) calls `DeploymentManager.delete_deployment()` (`src/cli/managers/deployment_manager.py:154`), which runs `shutil.rmtree(deployment_dir)` (`:201`) on `Path(ARCHI_DIR) / f"archi-{name}"` (`:170`). `ARCHI_DIR` defaults to `~/.archi`.
- `src/cli/managers/templates_manager.py:1306` `_copy_web_input_lists`: after the teardown, a list that is no longer a regular file gets a WARNING and is skipped (`:1328-1339`). The replacement deployment starts without that list.

The case the issue names: a config that points at a previous run's staged copy, `~/.archi/archi-<name>/weblists/<list>`. The failure is deterministic. Origin: the sync adversarial review of PR #781, finding "[high] Preflight accepts input lists that force teardown will delete", deferred to #819 because the path exists when the command starts, which is outside #617's acceptance criteria.

## What Changes

- `src/cli/source_registry.py`: a private generator `_configured_input_list_entries(configs)` that yields `(path, config file)` with the shape rules of `refuse_missing_input_lists()`, which now reads its entries from it (no behavior change: its 11 tests stay green with no edit). A new helper `refuse_input_lists_inside_deployment(configs, deployment_dir)` resolves each list path and the deployment directory with `Path.resolve()` and raises one `ValueError` that names every list inside the directory, the resolved directory, and each list's config file.
- `src/cli/cli_main.py`: `create` and `evaluate` call the helper on the lines after `refuse_missing_input_lists(...)`, only when `--force` is set, because the teardown runs only then. `restart` has no teardown and does not change.
- Tests: `tests/unit/test_refuse_input_lists_inside_deployment.py` (the helper, 15 items) and `tests/unit/test_cli_input_list_inside_deployment.py` (the two commands, 7 items). No existing test changes.
- Docs: `docs/docs/configuration.md` (one paragraph after the #617 paragraph in `### Sources`) and `docs/docs/data_sources.md` (one paragraph after the #617 paragraph).

Out of scope: option (b) of the issue, a staging copy outside the deployment directory made before the teardown (design D2 records why); `archi restart` (no teardown); `archi sources build`; `_copy_web_input_lists()` and the warn-and-skip of #636 and PR #689; the dry-run teardown notice; `~` expansion or any other change to how a list path resolves.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-create-preflight`: one ADDED requirement. A configured input list that resolves inside the deployment directory stops a forced `create` or `evaluate` before `remove_existing_deployment()`. It is ADDED, not MODIFIED: the spec on disk (`openspec/specs/cli-create-preflight/spec.md`) holds only the #287 requirements, because the #293, #311, and #617 deltas are not archived, so there is no input-list requirement to modify, and each current requirement stays true. This change adds one more refusal ahead of the teardown.

## Impact

- Code: `src/cli/source_registry.py` (1 generator, 1 helper, 1 loop that now uses the generator), `src/cli/cli_main.py` (1 import, 2 call sites of 3 lines each).
- Tests: 2 new files, 22 items. Measured on a scratch copy of `origin/dev` `f457d3d1` on 2026-11-09: before the code change the helper file fails at import and the CLI file is `4 failed, 3 passed` (each of the 4 reaches the `remove_existing_deployment` spy); after it, `22 passed`, and the 18 unit-test files that use `cli_main` plus the 5 input-list and evaluations-config test files are green (619 passed; the only 4 failures in that run read `scripts/` or `docs/`, which the scratch copy did not hold).
- Docs: 2 pages under `docs/docs/`.
- Behavior: a forced `create` or `evaluate` whose config lists a path inside `~/.archi/archi-<name>/` now exits 1 before the teardown, and the file is intact. Before, the teardown deleted the list and the deployment started without it. The FASRC dev redeploy does not change: `archi_deploy` runs `cd "$REPO_ROOT"` before `archi create` and dev's list is the config checkout's `lists/sources.list`, which `ensure_config` requires (`deploy/scripts/lib.sh`, as the #617 proposal records); that path is not under `~/.archi`.
