## Why

The chat agents read two settings from `services.chat_app` that no deployment can set (#670):
- `force_initial_retrieval`, read by `_inject_forced_retrieval` in `fasrc_docs_agent.py` and `cms_comp_ops_agent.py`, default on;
- `tool_budgets`, read by `BaseReActAgent._tool_budgets()` in `base_react.py`, default `{"search_vectorstore_hybrid": 2}`.

The config render template (`src/cli/templates/base-config.yaml`) lists the `chat_app` keys one by one and has neither, so both are dropped when a deployment's YAML is rendered. They never reach the config seeded into Postgres, and every deployment runs the code defaults. Config validation removes nothing (`config_manager.py:189`), so the template is the only place they are lost.

The docs call both settings configurable (`docs/docs/rag_architecture.md`, `docs/docs/benchmarking.md`). The permitted follow-up to the rung-0 prompt sweep (#540: forced retrieval off for both control and r0a) would silently run with forced retrieval on. With #669, the budget now counts only the model's own searches, which makes it worth being able to tune per deployment.

## What Changes

- **The template carries both keys.** It renders `services.chat_app.force_initial_retrieval` and `services.chat_app.tool_budgets` into the `chat_app` block only when a deployment declares them with a non-null value. Values pass through as JSON, so an explicit `false` stays a boolean and a budget map keeps its keys and numbers.
- **Undeclared or null means the code default.** The key is left out, so forced retrieval stays on and the per-turn budget stays 2. A `null` must not render: `.get("force_initial_retrieval", True)` would return `None` and silently turn forced retrieval off.
- **No new `default(<literal>, true)` call sites.** That filter turns an explicit `false` into the default (#448, fixed in #456), and the guards in `tests/unit/test_base_config_falsy_defaults_render.py` reject new ones.
- **Docs.**
  - `docs/docs/rag_architecture.md`: give the full `services.chat_app.tool_budgets` path where it is shortened (lines 88 and 224).
  - The same file, line 174: correct the claim that settings can be overridden per pipeline. The code reads `pipeline_config`, but `archi.pipeline_map` is not rendered either, so no deployment config reaches that layer.
- **Not in scope:**
  - Rendering `archi.pipeline_map` (a separate decision; see design).
  - The stale line reference in archi-config's `benchmarking/prompt_sweep_r0/README.md` (separate repository).
  - Varying either setting per arm inside one multi-config benchmark deployment. One deployment seeds one value (#523).

## Capabilities

### New Capabilities
- `chat-agent-config-render`: chat-agent settings declared under `services.chat_app` in a deployment config survive the render into the runtime config, with explicit `false` and maps preserved and undeclared or null values left to the code defaults.

### Modified Capabilities
<!-- none -->

## Impact

- **Code:** `src/cli/templates/base-config.yaml` (`chat_app` block only). No runtime code changes; the agents already read both keys.
- **Tests:** new `tests/unit/test_base_config_chat_agent_knobs_render.py`, modelled on `test_base_config_context_editing_render.py`. The existing falsy-default guards must pass unchanged.
- **Docs:** `docs/docs/rag_architecture.md`.
- **Deployments:** no archi-config file sets either key today, so after a redeploy every deployment renders exactly as before. Behaviour changes only when an environment declares a key.
- **Related:** #669 (budget counts only model searches), #540 (sweep follow-up that needs the flag), #523 (per-arm limit unchanged).
