## 1. Red tests (write first, watch them fail for the right reason)

- [ ] 1.1 New `tests/unit/test_base_config_chat_agent_knobs_render.py` (render helper as in
      `test_base_config_context_editing_render.py`): `force_initial_retrieval: false` renders
      `is False`; `true` renders `is True`
- [ ] 1.2 Same file: `force_initial_retrieval` undeclared → key absent; declared `None` → key
      absent
- [ ] 1.3 Same file: `tool_budgets: {search_vectorstore_hybrid: 3}` renders equal; undeclared →
      absent; `None` → absent

## 2. Implementation (minimum to turn 1.x green)

- [ ] 2.1 `src/cli/templates/base-config.yaml` `chat_app` block: emit each key inside
      `{% if <path> is defined and <path> is not none %}` with `| tojson`, next to
      `context_editing`, with a short comment on why null is skipped
- [ ] 2.2 `docs/docs/rag_architecture.md`: full `services.chat_app.tool_budgets` path (lines 88,
      224); correct the per-pipeline override claim (line 174) to say `archi.pipeline_map` is not
      rendered, so no deployment config reaches `pipeline_config`

## 3. Verification

- [ ] 3.1 `python -m pytest tests/unit/test_base_config_chat_agent_knobs_render.py
      tests/unit/test_base_config_falsy_defaults_render.py
      tests/unit/test_base_config_context_editing_render.py` green; the falsy-default baseline
      unchanged
- [ ] 3.2 Render dev's real environment config (`config/environments/dev.yaml`, which declares
      neither key) before and after: the rendered `chat_app` block is identical
- [ ] 3.3 `bash scripts/gate.sh` green
