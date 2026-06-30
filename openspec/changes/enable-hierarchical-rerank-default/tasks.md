## 1. Failing tests first (TDD red)

- [x] 1.1 In `tests/unit/test_base_config_chunking_render.py` (reuses the `_render` Jinja
      harness) or a new `tests/unit/test_base_config_retriever_render.py`, add
      `test_hierarchical_rerank_enabled_by_default`: render the template with no
      `data_manager.retrievers.hierarchical_rerank.enabled` set and assert
      `cfg["data_manager"]["retrievers"]["hierarchical_rerank"]["enabled"] is True`.
- [x] 1.2 Add `test_hierarchical_rerank_explicit_opt_out_renders_false`: render with
      `data_manager.retrievers.hierarchical_rerank.enabled = False` and assert the rendered
      value is `False` (operator opt-out still honored).
- [x] 1.3 Run the new tests and watch them FAIL for the right reason (current render is
      `enabled: false`): `conda run -n archi pytest tests/unit/test_base_config_chunking_render.py -k "default or opt_out" -q`.

## 2. Flip the shipped default (green)

- [x] 2.1 In `src/cli/templates/base-config.yaml` (~line 214), change
      `hierarchical_rerank.enabled` Jinja default from `default(false, true)` to
      **`default(true)`** (bare — boolean arg dropped). NOTE: the boolean form
      `default(true, true)` is WRONG here — it treats a falsy explicit `enabled: false` as
      "unset" and renders `true`, swallowing the operator opt-out. The opt-out guard test
      (1.2) caught this; `default(true)` applies the default only when the key is undefined.
- [x] 2.2 Update the adjacent template comment (currently "Disabled by default: when off,
      retrieval falls back to the hybrid_retriever above") to state it is **enabled by
      default** and that operators opt out with `enabled: false`.
- [x] 2.3 Run the new tests and confirm GREEN; confirm `factory.py` is left unchanged (the
      `.get("enabled", False)` runtime fallback stays conservative per design.md decision #2).

## 3. Gate + regression

- [x] 3.1 Run the full gate: `bash scripts/gate.sh` (format → lint → test, ≥80% diff
      coverage). Must pass before commit; never `--no-verify`.
- [x] 3.2 Confirm no existing render/retriever tests regressed (e.g.
      `test_base_config_chunking_render.py`, `test_hierarchical_rerank_ab_configs.py`,
      `test_retriever_factory.py`) — the A/B benchmark configs set `enabled` explicitly, so
      they are unaffected by the default flip.

## 4. Ship

- [ ] 4.1 Branch from `origin/dev`, commit (short lowercase message, no `Co-Authored-By`),
      push, open PR with `gh pr create --repo fasrc/archi --base dev` referencing ADR 0003;
      request `@codex review`.
- [ ] 4.2 Reply inline per review finding; merge once CI green and comments resolved.
- [ ] 4.3 Release: redeploy fasrc-dev (`archi-dev-deploy-verify`) so the live dev instance
      picks up the new default; confirm chat HTTP-200 and that retrieval logs show
      "hierarchical_rerank enabled". Then archive this change (`/opsx:archive`).
