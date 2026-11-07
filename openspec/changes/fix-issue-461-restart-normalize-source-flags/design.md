## Context

`restart` and `create` build `enabled_sources` the same way, but only `create` writes the
result back into the config dicts with `set_sources_enabled`. The deployed rendered config
under `<ARCHI_DIR>/archi-<name>/configs/*.yaml` therefore carries `enabled: false` for each
unselected managed source. A raw re-render of the same input config carries the template
default, `true`, for `git`, `sso`, `jira`, and `redmine`.

## Goals and non-goals

- Goal: the same input config that `create` accepted passes the restricted-section
  comparison in `restart`.
- Goal: a real change to a restricted section is still refused.
- Non-goal: change what `_validate_non_chatbot_sections` compares, or the template defaults.

## Decision

Call `config_manager.set_sources_enabled(enabled_sources)` in `restart` before
`validate_configs`, as the operator decided on 2026-09-26.

`create` calls it AFTER `validate_configs` (`cli_main.py:225` then `:249`). The issue decision
puts it BEFORE `validate_configs` in `restart`. The order relative to `validate_configs` does not
change validation: `validate_configs` receives `enabled_sources` as an argument, and
`set_sources_enabled` only adds `enabled` keys. The order that matters is that the call comes
before `_validate_non_chatbot_sections`. The tests assert both orders, as the issue acceptance
criteria require.

`set_sources_enabled` is idempotent, so a later call from another code path is harmless.

## Test seam

No unit test covers `restart` today. Drive `restart` through `click.testing.CliRunner`:

- Patch `cli_main.ARCHI_DIR` (it freezes at import time; see the `archi_home` fixture in
  `tests/unit/test_cli_create_dev_smoke.py:63-78`) and `cli_main.check_docker_available`.
- Build `<ARCHI_DIR>/archi-<name>/compose.yaml` with a `services:` map that contains the target
  service (for example `chatbot`) and `postgres`.
- Build `<ARCHI_DIR>/archi-<name>/configs/<name>.yaml` as the deployed rendered config. Produce
  it the way `create` does: load the input config, call `set_sources_enabled` with the resolved
  sources, then render it with `_render_config_for_compare(config, False, verbosity, env)`.
  Use `cli_main.env` for `env`.
- Stop the run after the comparison: patch `cli_main.ServiceBuilder.build_compose_config` to
  raise `RuntimeError(SENTINEL)`. Pass no `--env-file` and do not enable `grafana`, so the run
  reaches `build_compose_config` with `SecretsManager(None, config_manager)`. If that
  constructor fails in the test environment, patch `cli_main.SecretsManager` instead.
- "Passes the comparison" means the output contains `SENTINEL` and does not contain
  "Restart config changes are restricted".

For the call-order test, wrap `ConfigurationManager.set_sources_enabled`,
`ConfigurationManager.validate_configs`, and `cli_main._validate_non_chatbot_sections` with
recorders that call through, and assert the recorded order.

## Risks

- If an older `create` that did not normalize flags wrote the deployed config, the comparison
  changes direction for that deployment. `set_sources_enabled` was in `create` before #461 was
  filed, so this is not a current case.
