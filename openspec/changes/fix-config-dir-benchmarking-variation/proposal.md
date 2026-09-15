## Why

The `ragas-prompt-sweep` change shipped a generator that writes one benchmarking config per prompt and tells the user to run them with `archi evaluate --config-dir <sweep_dir>`. That command does not work. The multi-config loader (`src/cli/managers/config_manager.py`) treats `STATIC_FIELDS = ['global', 'services']` as constants that must be byte-identical across every config in a `--config-dir` run, but the sweep's whole purpose is to vary `services.benchmarking.agent_md_file` (and the derived `services.benchmarking.name`) per config. So the second and third configs are rejected with:

> The field services must be consistent across all configurations.

This was only discovered when the first real multi-config sweep ran (ragas-prompt-sweep task 8.3) — every prior benchmark run was single-config, so the wall was latent. The same wall blocks the lifted pairwise A/B path, which also varies the agent across configs.

The consistency check is correct for `archi create` (one deployment cannot have inconsistent services) but over-strict for `archi evaluate`, where each config is run independently and the results are aggregated. `services.benchmarking` is precisely the axis a benchmark `--config-dir` is meant to sweep. The safety the check was providing for benchmarking is already covered downstream: the leaderboard's `shared_context` cross-checks model/provider/judge/queries across configs and warns on drift.

## What Changes

Making `archi evaluate --config-dir` actually run a multi-config sweep required fixing three layers, each surfaced in turn by a live run:

- **Exempt `services.benchmarking` from the cross-config consistency check.** When the multi-config loader compares the `services` static field across configs, it compares `services` with the `benchmarking` subsection removed. `global` and every other `services.*` subsection (notably `services.chat_app`, which carries the SUT provider/base_url) MUST still be identical across configs.
- **Render one distinct config file per variant.** The deployment manager named every rendered config after the top-level `name`, so a sweep whose configs all share `name: ragas-bench` collided onto a single `ragas-bench.yaml` (only the last survived). Multi-config runs now render a distinct file per config (preferring the per-variant `services.benchmarking.name`), so the benchmarker iterates every variant. Single-config runs still render `config.yaml`.
- **config-seed tolerates per-variant filenames.** `config-seed` hardcoded `/rendered-config/config.yaml` and aborted the whole deployment when a multi-config run rendered per-variant files instead. It now falls back to the first `*.yaml` in the rendered-config directory. (config-seed is chatbot infrastructure; the benchmarker reads the YAML files directly and never consumes the seeded `static_config`, so seeding from any one config is harmless.)
- **Net effect.** `archi evaluate --config-dir <sweep_dir>` accepts configs that differ only in `services.benchmarking`, renders them to distinct files, seeds without aborting, and the benchmarker runs every variant — so the prompt sweep and pairwise A/B paths run as documented. `archi create` and single-config `archi evaluate` are unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `benchmarking-config-dir`: The multi-config loader's static-field consistency check now exempts `services.benchmarking`, allowing a `--config-dir` benchmarking run to sweep prompt/benchmarking variants while still enforcing identical `global` and non-benchmarking `services`.

## Impact

- **Code**: `src/cli/managers/config_manager.py` — `_append` consistency comparison for `services` excludes the `benchmarking` key. `src/cli/managers/templates_manager.py` — `_render_config_target_name` derives a distinct filename per config in multi-config mode. `src/cli/tools/config_seed.py` — `resolve_config_path` falls back to the first `*.yaml` when `config.yaml` is absent.
- **Tests**: New unit tests — loader (`test_config_manager_benchmarking_variation.py`), filename derivation (`test_render_config_filename.py`), config-seed resolution (`test_config_seed_resolve.py`).
- **Behavior**: Unblocks `ragas-prompt-sweep` task 8.3 (the live sweep) and the pairwise A/B `--config-dir` path. `archi create` and single-config `archi evaluate` are unaffected.
- **Docs**: No doc change required; `docs/docs/benchmarking.md` already documents the `--config-dir` workflow that this makes functional.
