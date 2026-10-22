# Stage the evaluations agent config at create time

## Why

An enabled evaluation console needs the file that `services.chat_app.evaluations.agent_config_path`
names to exist inside the chatbot container. Today nothing makes sure it does:

- `validate_evaluations_config()` (`src/utils/evaluations_config.py:15-53`) refuses a blank
  path and the live-config path, but it never checks that the file exists.
- `QAConsoleService.create_run()` (`src/evaluation/qa/console.py:441`) fails every run when
  the file is absent, after the console has registered.
- The documented workaround (`docs/docs/configuration.md:407-421`) tells the operator to put
  a redacted copy in `<base_dir>/configs/`. `archi create --force` calls
  `remove_existing_deployment()` → `delete_deployment(remove_files=True)`
  (`src/cli/utils/helpers.py:316-350`), which deletes `<base_dir>/`. So every
  `redeploy.sh` destroys the file.

This is issue #371 (milestone v2026.11.0, P2, default tier). The operator chose option B on
2026-09-26: stage `agent_config_path` the way `mcp_config_path` is staged. The issue body is
the authoritative work order.

## What Changes

- `agent_config_path` becomes a **host** path in the operator's config: absolute, or
  relative to the deployment YAML (the same rule as `mcp_config_path`).
- New `resolve_agent_config_source(config)` in `src/utils/evaluations_config.py`. For an
  enabled console it resolves the host path and refuses a file that is missing, is not a
  regular file, is not readable, or is the operator's own deployment YAML.
- `ConfigurationManager._validate_chat_app_config()` calls it. This runs in
  `validate_configs()`, above `remove_existing_deployment()` in both `create` and
  `evaluate`, so a missing file is refused before any teardown.
- New `refuse_agent_config_inside_deployment(configs, base_dir)` in the same module, with a
  thin call in `create` and `evaluate` above the teardown. It refuses a source file inside
  `<base_dir>/`, because the teardown deletes that directory before the stage copies the
  file. This is exactly where the old docs told operators to put it.
- New `_stage_agent_config()` in `TemplateManager`, run right after
  `_stage_evaluation_config()`. It copies the file to
  `<base_dir>/evaluation_config/qa_agent_config.yaml`.
- `_render_config_files()` rewrites the runtime `agent_config_path` to
  `/root/archi/evaluation_config/qa_agent_config.yaml` when the file was staged.
- `base-compose.yaml` mounts `./evaluation_config` read-only when either the MCP registry or
  the agent config was staged (today: only the MCP registry).
- `docs/docs/configuration.md`: the operator names a host file; Archi copies it on every
  create; the operator is still responsible for redacting it.

## Out of scope

- Generating or redacting the agent config (#320's redaction child, #562).
- `mcp_config_path` inside `<base_dir>/` has the same teardown flaw. This change does not
  touch it; the PR body notes it.
- `deploy/**` and the live dev flip.
- The #294 wider ordering work (PR #581). This change needs no reordering: config
  validation already runs above the teardown.

## Impact

- Code: `src/utils/evaluations_config.py`, `src/cli/managers/config_manager.py`,
  `src/cli/managers/templates_manager.py`, `src/cli/cli_main.py` (two thin call sites),
  `src/cli/templates/base-compose.yaml`.
- Tests: `tests/unit/test_evaluations_config_validation.py`,
  `tests/unit/test_evaluation_config_staging.py`, `tests/unit/test_evaluation_config.py`,
  `tests/unit/test_evaluations_root_validation.py`, `tests/unit/test_cli_create_dev_smoke.py`.
- Docs: `docs/docs/configuration.md`.
- Behavior change for operators: `agent_config_path` is now read on the host at create
  time. A relative value resolves against the deployment YAML's directory, not against
  `/root/archi`. A value that names a file only present in the container (for example
  `/root/archi/configs/config.eval.yaml`) is now refused at create time with a message that
  names the resolved host path.
