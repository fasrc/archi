## Context

`ConfigManager._append` (`src/cli/managers/config_manager.py`) accumulates the configs loaded from a `--config-dir`. For each `static_field` in `STATIC_FIELDS = ['global', 'services']`, it requires the field to be present in every config and **equal** to the previous config's value, raising `"The field <f> must be consistent across all configurations."` otherwise. This loader is shared by `archi create` and `archi evaluate`.

The `ragas-prompt-sweep` generator writes configs that are identical except for `services.benchmarking.agent_md_file` and `services.benchmarking.name`. Because `services` is compared whole, those configs fail the check, so the documented `archi evaluate --config-dir` sweep cannot run. The pairwise A/B path varies the agent the same way and is blocked identically.

## Goals / Non-Goals

**Goals**
- Allow a `--config-dir` benchmarking run to include configs that differ only within `services.benchmarking`.
- Keep `global` and all non-benchmarking `services.*` (especially `services.chat_app`, the SUT provider/base_url) strictly identical across configs.
- Leave `archi create` and single-config `archi evaluate` behavior unchanged.

**Non-Goals**
- Reworking the generator or the leaderboard (already shipped in `ragas-prompt-sweep`).
- Allowing arbitrary `services.*` drift — only `services.benchmarking` is exempted.
- Making the loader command-aware (no `create` vs `evaluate` branching).

## Decisions

### D1. Exempt `services.benchmarking` by comparing `services` minus that key

In the `_append` equality check, when `static_field == 'services'`, compare the two `services` dicts with the `benchmarking` subsection removed (e.g. `{k: v for k, v in services.items() if k != 'benchmarking'}`) rather than the raw dicts. `global` is compared unchanged. Presence checks are unchanged.

Rationale: `services.benchmarking` is the sweep axis; every other static field still guarantees the variants are run against the same SUT and globals. The comparison is narrowed, not removed.

### D2. Safe for `archi create`

`archi create` does not consume `services.benchmarking`, so exempting it from the cross-config equality check cannot change a created deployment. Two create configs that differed only in `services.benchmarking` would have produced identical deployments anyway; now they simply do not raise. No create path reads the exempted key.

### D3. Downstream drift guard already exists

The leaderboard's `shared_context` (from `ragas-prompt-sweep`) cross-checks `model`, `provider`, `evaluator_model`, and `queries_path` across the swept configs and records a warning if any differ. So even though `services.benchmarking.model` is now allowed to vary at load time, a genuinely non-comparable sweep is still surfaced to the user at aggregation time. The loosened loader does not weaken the apples-to-apples guarantee.

## Risks / Trade-offs

- **Over-permissiveness within `services.benchmarking`.** A sweep could now differ in benchmarking fields beyond the prompt (e.g. `queries_path`, `model`). Mitigated by the leaderboard `shared_context.warnings`, which flags exactly those mismatches without blocking the run. Accepted: a benchmark loader should rank what it is given and warn, not refuse to load.
- **Shared loader.** The change lives in code used by `create` too. Mitigated by D2 (create ignores the key) and by a unit test asserting non-benchmarking `services` drift is still rejected.

## Migration / Rollout

No migration. The change is backward-compatible: any config set that loaded before still loads (identical `services` trivially remain equal after removing `benchmarking`). The only new behavior is that configs differing solely within `services.benchmarking` now load instead of raising. Unblocks `ragas-prompt-sweep` task 8.3 immediately on the next `archi evaluate --config-dir` run.
