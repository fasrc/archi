## Why

A multi-config benchmarking deployment renders one YAML file per arm and no `config.yaml`.
`config_seed.resolve_config_path` falls back to the alphabetically first file and the seeder
writes that file's whole `services` block (and `data_manager`, `global`, ...) into Postgres.
The agent reads the seeded `static_config`, not its own arm file. So every setting outside
`services.benchmarking` that differs between arms silently takes the first arm's value on
every arm. `services.chat_app.force_initial_retrieval` is the concrete case: its docstring
in `fasrc_docs_agent.py` invites a per-arm A/B that cannot happen. Procedure E refuses such a
sweep only after the compute is spent. The `resolve_config_path` docstring says the opposite
("Seeding from any one config is harmless").

Decision (operator, 2026-09-26, recorded on #523): fix the docstrings and fail the
config-seed step closed. Warn-only and per-arm runtime settings were rejected.

## What Changes

- Add `arm_config_divergence(paths)` to `src/cli/tools/config_seed.py`. It loads each arm
  file and reports, per file, the dotted paths where it differs from the first file. It
  ignores `DIVERGENCE_IGNORED_PATHS` (`services.benchmarking`, the deploy-rewritten
  `agents_dir`/`skills_dir` paths, and `name`) and reuses `asserted_config_divergence` from
  `src/utils/benchmark_provenance.py` in both directions, so the check is symmetric.
- In `seed_entry`, on the fallback path only (no `config.yaml`, more than one `*.yaml`): if
  any arm differs, print the file names and the diverging paths to stderr and exit non-zero
  **before** any Postgres connection. `base-compose.yaml` already makes every dependant
  wait on `service_completed_successfully`, so the deployment stops.
- Correct the `resolve_config_path` docstring, the module docstring of
  `tests/unit/test_config_seed_resolve.py`, and the `force_initial_retrieval` docstring in
  `src/archi/pipelines/agents/fasrc_docs_agent.py`.

**BREAKING (accepted by the operator):** a current multi-config deployment whose arms
differ outside the ignored paths now fails at config-seed. Such a sweep was already invalid.

## Capabilities

### New Capabilities

### Modified Capabilities
- `retrieval-benchmarking`: ADDED requirement — the config seeder refuses a multi-arm
  deployment whose arm files disagree outside the arm-override, deploy-rewritten and `name`
  paths.

## Impact

- Code: `src/cli/tools/config_seed.py`, docstring in
  `src/archi/pipelines/agents/fasrc_docs_agent.py`.
- Tests: new `tests/unit/test_config_seed_arm_divergence.py`; docstring in
  `tests/unit/test_config_seed_resolve.py`.
- No change to `src/utils/benchmark_provenance.py` behavior, `compare_runs.py`, `deploy/**`,
  `config/**`, or `src/cli/templates/base-compose.yaml`.
