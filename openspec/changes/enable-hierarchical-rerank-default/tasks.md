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

## 2b. Pair the chunking default (review finding, PR #78 Codex P2)

- [x] 2b.1 RED: add `test_default_chunking_strategy_is_hierarchical` and
      `test_default_retrieval_config_is_coherent` — render with no chunking/retriever settings
      and assert `chunking.strategy == "sentence"` (and reranker enabled). Watch them fail
      (current default renders `character`).
- [x] 2b.2 GREEN: flip `data_manager.chunking.strategy` Jinja default `character → sentence`
      in `base-config.yaml` (~line 187) and update its comment. Rationale: the reranker only
      returns parent context when ingestion built parent/child nodes (`sentence`/`markdown`);
      `character` produces flat chunks with no `parent_id`, so default-on rerank would pay its
      cost with no parent-context benefit. The two defaults must flip together to match the
      ADR 0003 treatment. `manager.py`'s `.get("strategy", "character")` runtime fallback left
      conservative.

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
