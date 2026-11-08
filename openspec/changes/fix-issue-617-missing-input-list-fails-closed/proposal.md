## Why

A configured `input_lists` path that does not exist does not stop a deploy (issue #617). On `origin/dev` `1ca97e24`:

- `src/cli/managers/config_manager.py:509`: `get_enabled_sources()` logs `Input list path not found, skipping` and continues.
- A source that only the missing list enables (a `git-`, `sso-`, `elog-`, or `indico-` entry) therefore stays out of `enabled_sources`.
- `set_sources_enabled()` then writes `enabled: false` for that source: `src/cli/cli_main.py:253` (`create`), `:618` (`restart`), `:901` (`evaluate`). The collector goes off, and the only sign is one WARNING line.

A relative path resolves from the current directory (cwd). An operator who runs `archi` from another directory loses those sources with no error. On 2026-10-20 the operator decided to keep the cwd rule and to fail closed (issue body, "Decision"). The rejected alternative, a path relative to the config file, would move dev's `config/lists/sources.list` to a path under `config/environments/` and drop every source.

## What Changes

- `src/cli/source_registry.py`: a new helper, `refuse_missing_input_lists(configs)`. It reads each loaded config's `data_manager.sources.links.input_lists`. It raises one `ValueError` that names each path that does not exist (`not os.path.exists(path)`), the config file that lists it, and the cwd.
- `src/cli/cli_main.py`: `create`, `restart`, and `evaluate` call the helper on the line after `ConfigurationManager(...)`. This is before `set_sources_enabled()` and before any teardown. `restart` has no outer exception handler, so its call site changes the `ValueError` into a `click.ClickException`.
- Tests: `tests/unit/test_refuse_missing_input_lists.py` (the helper) and `tests/unit/test_cli_missing_input_lists.py` (the three commands).
- Docs: `docs/docs/configuration.md` (the Sources section), `docs/docs/data_sources.md` (the paragraph that says a missing list does not stop `archi create`), and `examples/benchmarking/hierarchical_rerank_ab/` (`README.md` and the comments in the two arm configs, which say a missing list is skipped).

Out of scope (issue #617): any change to path resolution; the warn-and-skip for an existing list that cannot be read (#636, PR #689); the data-manager runtime, which reads the staged copies in `weblists/`; archi-config; `archi sources build`, which writes the list and so can run before the list exists.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cli-create-preflight`: one ADDED requirement. A configured input list path that does not exist stops `create`, `restart --config`, and `evaluate` before `set_sources_enabled()` and before any teardown. It is ADDED, not MODIFIED, because each current requirement stays true. This change adds one more refusal ahead of the teardown.

## Impact

- Code: `src/cli/source_registry.py` (1 helper and `import os`), `src/cli/cli_main.py` (1 imported name and 3 call sites).
- Tests: 2 new files.
- Docs: 2 pages under `docs/docs/`, 1 README, and 2 YAML comment blocks under `examples/`.
- Behavior: a config whose list path does not exist from the cwd now fails at once with exit status 1. Before, it deployed without the sources of that list. The FASRC dev redeploy does not change: `archi_deploy` runs `cd "$REPO_ROOT"` before `archi create` (`deploy/scripts/lib.sh:388`), and `ensure_config` already refuses a config checkout without `lists/sources.list` (`deploy/scripts/lib.sh:370`).
