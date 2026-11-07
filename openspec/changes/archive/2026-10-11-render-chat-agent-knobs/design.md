## Context

`archi create` renders a deployment's YAML through `src/cli/templates/base-config.yaml` (Jinja, `ChainableUndefined`). Only the keys the template emits reach the rendered runtime config, which the config seed loads into Postgres and the app reads. The `chat_app` block (`base-config.yaml:169-240` at `origin/dev` `7406721b`) lists its keys one by one. `context_editing` (`:174-182`) is the precedent for an optional block: it is emitted only when declared, as `tojson`, and pinned by `tests/unit/test_base_config_context_editing_render.py`.

The readers already exist:
- `force_initial_retrieval`: `self._chat_app_config.get("force_initial_retrieval", True)` in both agents' `_inject_forced_retrieval`.
- `tool_budgets`: `BaseReActAgent._tool_budgets()` merges `DEFAULT_TOOL_BUDGETS`, then `services.chat_app.tool_budgets`, then `pipeline_config.tool_budgets`. It logs and skips non-integer and non-positive values.

## Goals / Non-Goals

**Goals:** a deployment can turn forced retrieval off and set per-tool turn budgets from its YAML. An explicit `false` stays `false`. Undeclared keys leave the code defaults in one place (the agents), not duplicated into the template.

**Non-Goals:**
- Rendering `archi.pipeline_map` (the per-pipeline layer).
- Validating budget values in the template (the runtime already does).
- Per-arm variation inside one multi-config benchmark deployment (#523).
- Editing archi-config.

## Decisions

**D1: emit only when declared and not null; no default in the template.** Each key is wrapped in `{% if <path> is defined and <path> is not none %}` … `{% endif %}`. Writing a default (`true`, `{}`) would fork the source of truth: the code already has defaults, and a template copy would drift.

The alternative, `| default(true, true)`, is exactly the falsy-default bug class (#448/#456). The guard tests reject new such call sites.

**D2: values through `tojson`.** It keeps booleans as booleans and a map as a map with its keys, and the YAML loader reads the JSON back losslessly. This matches `context_editing` and `tools`.

**D3: null is "not declared".** `context_editing` checks only `is defined`, so an explicit `null` renders as `null`. For `force_initial_retrieval` that would reach `.get(..., True)` as `None` and turn forced retrieval *off*, which is the opposite of what an empty value suggests. Treating null as undeclared keeps both keys on their defaults.

**D4: `archi.pipeline_map` stays unrendered (out of scope).** Everything but the template exists for it: the seed reads `archi_config` (`config_seed.py`), and five readers use `pipeline_config` (models, prompts, recursion_limit, tool_budgets, context_editing). Rendering it is a broader decision about per-pipeline overrides, with its own tests and docs. The docs are corrected to say that layer is not reachable from deployment config, and a follow-up issue can be filed if wanted.

## Risks / Trade-offs

- [A string such as `"false"` renders as a string and is truthy at runtime, so forced retrieval stays on] → YAML `false` is a boolean. Quoting it is an operator error that the template does not coerce, since coercing would also mangle a deliberate `"true"`. The docs show the unquoted form.
- [An invalid budget value (e.g. `0`, `"two"`) renders unchanged] → `_tool_budgets()` logs a warning and keeps the default, as it already does for `pipeline_config`.

## Migration Plan

No data or schema change, and no archi-config file declares either key, so a redeploy renders every deployment exactly as before. To use it, declare the key in the environment's YAML (archi-config PR plus a pin bump), then redeploy. Rollback is reverting the PR.

## Open Questions

- Should `archi.pipeline_map` be rendered (D4)? If yes, file it separately.
