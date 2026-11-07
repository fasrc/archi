## Why

`archi restart --config` (or `--config-dir`) refuses a config that `archi create` accepted, for
no change the operator made. Fixes fasrc/archi#461.

On `origin/dev` @ `db701852`, `create` calls `config_manager.set_sources_enabled(enabled_sources)`
(`src/cli/cli_main.py:249`) before it renders the deployed config. That call writes
`enabled: false` for each managed source that is not selected and has no `enabled` key
(`src/cli/managers/config_manager.py:454-472`). `restart` never calls it. `restart` then runs
`_validate_non_chatbot_sections` (`src/cli/cli_main.py:616`, `src/cli/utils/helpers.py:232-267`),
which renders the RAW new config (`_render_config_for_compare`, `helpers.py:195-210`) and compares
its `data_manager` section with the deployed one. The template default for an omitted
`enabled` key is `true` for `git`, `sso`, `jira`, and `redmine`
(`src/cli/templates/base-config.yaml`). As a result, the sections differ and `restart` raises
"Restart config changes are restricted to chatbot settings only".

The operator decided the fix on 2026-09-26 (issue body, "Decision"): call
`set_sources_enabled(enabled_sources)` in `restart` before `validate_configs` and before
`_validate_non_chatbot_sections`. "Close as not a bug" was rejected.

## What Changes

- In `restart` (`src/cli/cli_main.py:534`), directly after
  `enabled_sources = source_registry.resolve_dependencies(enabled_sources)` and directly before
  `config_manager.validate_configs(enabled_services, enabled_sources)` (`:614`), call
  `config_manager.set_sources_enabled(enabled_sources)`.
- `_validate_non_chatbot_sections` reads `config_manager.get_configs()`, which returns the same
  list that `set_sources_enabled` mutated in place, so the comparison sees the normalized flags.
- A real `data_manager` change (for example `git.enabled: true` where the deployed config has
  `false`) is still refused.
- No change to `create`, `evaluate`, `_validate_non_chatbot_sections`, the template defaults,
  or `get_enabled_sources`.
- Not BREAKING. A restart that succeeds today succeeds identically: `set_sources_enabled` only
  adds `enabled` keys that are absent, or sets `true` on sources that are already selected.

## Capabilities

### New Capabilities

- `cli-restart-source-flags`: `archi restart` with a new config normalizes the source `enabled`
  flags exactly as `archi create` does, before it validates or compares the config.

### Modified Capabilities

(none)

## Impact

- `src/cli/cli_main.py` — the `restart` command only, one added call.
- `tests/unit/test_cli_restart_source_flags.py` — new test file, three tests.
- No dependency, API, schema, or deployment changes. No container rebuild for the tests.
- Out of scope: the restricted-path semantics of `_validate_non_chatbot_sections`, template
  defaults, `links_enabled`, and the list-prefix inference in `get_enabled_sources` (#460,
  open as PR #613). Both changes touch the `enabled_sources` lines in `restart`; if #613 merges
  first, rebase and keep both.
