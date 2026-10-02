# Design — stage `evaluations.agent_config_path` at create time

## Context

Anchors verified on `origin/dev` `e58a7ada`:

- `src/utils/evaluations_config.py:15-53` — `validate_evaluations_config(chat_app_config)`.
  A pure string check: blank path refused; a path that normalizes (joined to
  `/root/archi`) to `LIVE_AGENT_CONFIG_PATH` refused. No filesystem access.
- `src/cli/managers/config_manager.py:72` — every loaded config carries
  `config["_config_path"]`, the operator's YAML path. `:181` —
  `_validate_chat_app_config(config, services)`; `:206` calls
  `validate_evaluations_config(chat_cfg)`, then the evaluations root check.
- `src/cli/cli_main.py:224` (`create`) and `:873` (`evaluate`) call
  `config_manager.validate_configs(...)`; the teardown `remove_existing_deployment(...)`
  is at `:294` and `:906`. `base_dir` is set at `:186` and `:818`.
- `src/cli/managers/templates_manager.py:89-93` — `EVALUATION_CONFIG_DIR`,
  `EVALUATION_MCP_CONFIG_FILENAME`, `EVALUATION_MCP_RUNTIME_PATH`. `:186-194` —
  `TemplateContext` with `evaluation_mcp_configured: bool = False`. `:504-521` —
  `_build_workflow()` stage list. `:663-729` — `_stage_evaluation_config()` (the
  precedent). `:862-917` — `_render_config_files()`; the `mcp_config_path` rewrite is at
  `:885-890`. `:1120` — `template_vars["evaluation_mcp_configured"]`.
- `src/cli/templates/base-compose.yaml:272-274` — the `./evaluation_config` mount, gated on
  `evaluation_mcp_configured`.
- `src/cli/templates/base-config.yaml:188` — renders `agent_config_path` with `tojson`.
- `src/interfaces/chat_app/evaluation_console.py:95-125` — the runtime seam. It refuses the
  live config by file identity; the fixed staged path is never the live config, so it
  needs no change.
- Test doubles: `tests/unit/test_evaluation_config_staging.py` builds the context as a
  `SimpleNamespace` with no `evaluation_mcp_configured` attribute until the stage sets it.
  `tests/unit/test_evaluations_root_validation.py:101-124` builds an enabled config whose
  `agent_config_path` is `/root/archi/configs/agent_config.eval.yaml` and has no
  `_config_path`.
- `black --check` passes on all four source files at `e58a7ada`.

## Decisions

### D1 — Keep the pure check; add a host check beside it

`validate_evaluations_config()` stays as it is and keeps running first. Its container-string
refusal is now a conservative extra refusal (a host path that spells the container's live
path is refused too), and it keeps parity with the runtime seam that
`test_preflight_verdict_matches_the_runtime_seam` pins. Only its docstring changes: it must
say that the value is a host path since #371, and that the container join is retained as a
conservative refusal.

New, in `src/utils/evaluations_config.py`:

```python
AGENT_CONFIG_STAGED_FILENAME = "qa_agent_config.yaml"

def resolve_agent_config_source(config: Dict[str, Any]) -> Optional[Path]:
    """Return the resolved host source for an enabled console, else None."""
```

`config` is the whole loaded config (it needs `_config_path`). Rules, in order:

1. Not enabled (`services.chat_app.evaluations.enabled is not True`) → `None`.
2. Call `validate_evaluations_config(chat_app)` (blank and live-string refusals).
3. `Path(raw).expanduser()`; if relative, join to `Path(config["_config_path"]).parent`
   and `.resolve()`. A relative value with no `_config_path` → `ValueError` naming the key.
4. Missing → `ValueError` whose message contains the dotted key, the words `not found`, and
   the resolved host path. Not a regular file → `ValueError` with `must be a file`.
   Not readable (`os.access(path, os.R_OK)` is false) → `ValueError` with `not readable`.
5. `_config_path` exists and `os.path.samefile(source, _config_path)` → `ValueError` naming
   the key and the phrase `live deployment config` (same words as the existing message).
6. Return the resolved `Path`.

Every message contains `services.chat_app.evaluations.agent_config_path`.

### D2 — Validation runs above the teardown

`_validate_chat_app_config()` calls `resolve_agent_config_source(config)` right after the
existing evaluations root check. `validate_configs()` runs above
`remove_existing_deployment()` in both `create` and `evaluate`, so a missing file costs no
running deployment. No reordering of `cli_main.py` is needed, and this change does not
depend on PR #581.

### D3 — Refuse a source inside the deployment directory

The copy happens in the template stage, which runs below the teardown. A source under
`<base_dir>/` passes D1, then the teardown deletes it, then the stage fails. New:

```python
def refuse_agent_config_inside_deployment(configs, base_dir: Path) -> None:
```

For each config, `source = resolve_agent_config_source(config)`; if `source` is not `None`
and `source.is_relative_to(base_dir.expanduser().resolve())` → `ValueError` naming the key,
the source path, and the deployment directory, and saying that `archi create --force`
deletes that directory — move the file outside it. `create` and `evaluate` each call it
once, with `config_manager.get_configs()` and `base_dir`, directly above
`remove_existing_deployment(...)`. Two thin lines in `cli_main.py`; the logic is in the
tested module.

### D4 — The stage and the rewrite

- `TemplateContext` gets `evaluation_agent_config_staged: bool = False`.
- `_stage_agent_config(context)` runs right after `_stage_evaluation_config` in
  `_build_workflow()`. It reads `context.config_manager.config` (the same source the MCP
  stage reads). `staged = base_dir / EVALUATION_CONFIG_DIR / AGENT_CONFIG_STAGED_FILENAME`.
  `source = resolve_agent_config_source(config)`. `None` → set the flag `False`, unlink a
  stale `staged` file if it exists (or is a symlink), return. Else `mkdir(parents=True,
  exist_ok=True)`, `shutil.copyfile(source, staged)`, flag `True`, log one info line.
- `EVALUATION_AGENT_CONFIG_RUNTIME_PATH = f"/root/archi/{EVALUATION_CONFIG_DIR}/{AGENT_CONFIG_STAGED_FILENAME}"`
  in `templates_manager.py`, imported filename from `evaluations_config.py` so there is one
  name.
- `_render_config_files()`: beside the `mcp_config_path` rewrite, if
  `getattr(context, "evaluation_agent_config_staged", False)` → set
  `evaluations_cfg["agent_config_path"] = EVALUATION_AGENT_CONFIG_RUNTIME_PATH`. Otherwise
  leave the value unchanged (a disabled console's value is inert). `getattr`, because the
  existing staging tests use a `SimpleNamespace` context.
- `_render_compose_file()`: `template_vars["evaluation_agent_config_staged"] =
  context.evaluation_agent_config_staged`; the compose guard becomes
  `evaluation_mcp_configured | default(false) or evaluation_agent_config_staged | default(false)`.

### D5 — Multi-config deployments

`_stage_agent_config` stages from `context.config_manager.config` (the primary config), as the
MCP stage does. `_render_config_files` applies the rewrite to every rendered config, as the
MCP rewrite does. D3 checks every config. This mirrors the precedent exactly; one staged file
per deployment.

## Risks

- Operators with a container-only value (the old workaround) get a refusal at create time.
  That is the intent: the old value was destroyed by every `--force`. The message names the
  resolved host path, and the docs change explains the move.
- Existing tests that build an enabled console with a nonexistent path now fail validation.
  Known: `tests/unit/test_evaluations_root_validation.py` `_chat_app_config`. Fix them by
  naming a real `tmp_path` file, never by weakening the check.
