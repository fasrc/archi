## 1. Loosen the cross-config consistency check

- [x] 1.1 In `src/cli/managers/config_manager.py` `_append`, when comparing the `services` static field across configs, compare both sides with the `benchmarking` subsection excluded (`_comparable_static_field` helper returns `{k: v for k, v in value.items() if k != 'benchmarking'}` for the `services` field). Compare `global` unchanged.
- [x] 1.2 Keep the existing presence checks (`must be present in all configurations`) and the error message wording for genuine mismatches unchanged. Only the equality comparison for `services` is narrowed.
- [x] 1.3 Run pyright on `config_manager.py`; confirm no new errors vs baseline. *(LSP/pyright: the only errors are pre-existing `reportReturnType` at lines 348–360 in unrelated methods; the added `_comparable_static_field` helper and the narrowed `_append` comparison introduce zero new diagnostics.)*

## 2. Unit tests for the loader

- [x] 2.1 Add `tests/unit/test_config_manager_benchmarking_variation.py`.
- [x] 2.2 Assert two configs identical except for `services.benchmarking.agent_md_file` (and `.name`) both load without raising (`len(configs) == 2`).
- [x] 2.3 Assert a config that differs in `global` still raises `"must be consistent across all configurations"`.
- [x] 2.4 Assert a config that differs in a non-benchmarking `services` subsection (e.g. `services.chat_app`) still raises.
- [x] 2.5 Assert backward compatibility: a set of byte-identical configs still loads (regression guard that the narrowed comparison did not change the equal case).

## 3. Live verification — the unblocked sweep

- [ ] 3.1 Regenerate the sweep configs from `config/benchmarking/prompt_sweep.yaml` (the three archived `fasrc-cannon` variants) with `scripts/benchmarking/generate_prompt_sweep.py`.
- [ ] 3.2 Run `archi evaluate -n bench-sweep --config-dir bench_out/sweep_configs -e ~/.archi/.env.benchmark --hostmode -f` against the local Qwen SUT + HUIT Bedrock judge (supply `HUIT_API_KEY` via the benchmark env file).
- [ ] 3.3 Confirm all three configs load (no "must be consistent" error) and the dump JSON contains a `leaderboard` with three ranked rows and a populated `shared_context` (matching model/judge, empty `warnings`). This closes `ragas-prompt-sweep` task 8.3.

## 4. Verification

- [x] 4.1 Run the full unit suite; confirm the new loader tests pass and no existing tests regress. *(282 passed, +9 new; the only failure is the pre-existing `test_loader_returns_content` ingestion test, unrelated.)*
- [x] 4.2 Confirm `archi create` is unaffected (single-config and the normal deploy path still validate as before). *(Single-config still renders `config.yaml`; the consistency exemption only narrows the `services` comparison; create never reads `services.benchmarking`.)*

## 5. Distinct rendered filenames for multi-config runs

*(Surfaced by the first live `--config-dir` run: all configs share top-level `name: ragas-bench`, so `_render_config_files` rendered them all to `ragas-bench.yaml` — only the last survived.)*

- [x] 5.1 Add `_render_config_target_name(single_mode, top_level_name, benchmarking_name, index, used_names)` to `src/cli/managers/templates_manager.py`: single-config → `config.yaml`; multi-config → `{services.benchmarking.name or top_level_name}.yaml`, disambiguated with `_{index}` on collision.
- [x] 5.2 Wire it into `_render_config_files` (enumerate configs, track `used_names`, read `services.benchmarking.name` in benchmarking mode).
- [x] 5.3 Unit tests `tests/unit/test_render_config_filename.py` (single→config.yaml; distinct files per variant; collision→index; fallback to top-level name). Pyright: no new errors vs baseline.

## 6. config-seed tolerates multi-config benchmarking deployments

*(Surfaced by the second live run: config-seed aborted the whole compose with `FileNotFoundError: /rendered-config/config.yaml` because multi-config renders per-variant files, not `config.yaml`.)*

- [x] 6.1 Add `resolve_config_path(config_path)` to `src/cli/tools/config_seed.py`: return the file if it exists, else fall back to the first `*.yaml` in the rendered-config dir (config-seed is chatbot infra; the benchmarker reads YAML directly and ignores the seeded static_config). Wire it into `seed_entry`.
- [x] 6.2 Unit tests `tests/unit/test_config_seed_resolve.py` (existing file passthrough; missing config.yaml → first sorted yaml; dir → first yaml; nothing → original path). Pyright: no new errors vs baseline.

## 7. Stage every variant's agent prompt

*(Surfaced by the third live run: config 1 ran fully, then config 2 crashed with `FileNotFoundError: /root/archi/agents/fasrc-cannon-v2-lean.md` — `_stage_agents` copied only the first config's `agent_md_file`.)*

- [x] 7.1 In `src/cli/managers/templates_manager.py` `_stage_agents`, for benchmarking mode iterate `context.config_manager.get_configs()` and stage each config's `agent_md_file` (resolving each via its own `_config_path`), not just the primary config.
- [x] 7.2 Unit test `tests/unit/test_stage_agents_multiconfig.py`: three configs with distinct agent files all land in `data/agents/`. Pyright: no new errors vs baseline.

## 8. End-to-end verification

- [x] 8.1 `archi evaluate --config-dir` renders 3 distinct config files (no collision), config-seed completes, and all 3 agent prompts stage — confirmed live.
- [x] 8.2 Confirm the run produces a dump with a 3-row `leaderboard` + populated `shared_context`. **Closes ragas-prompt-sweep task 8.3.** *(Live: `bench_out/benchmarking-bench-sweep-20260610_015120.json` — 3 ranked rows (v2-lean / v1-strict / v3-cited by faithfulness), `shared_context` populated with model/judge/queries/corpus_snapshot_id, `warnings: []`.)*
