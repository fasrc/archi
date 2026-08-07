## MODIFIED Requirements

### Requirement: Cross-config consistency for a `--config-dir` run

When multiple configs are loaded from a `--config-dir`, the loader SHALL require `global` and `services` to be present in every config. It SHALL require `global` to be identical across all configs. It SHALL require `services` to be identical across all configs **except for the `services.benchmarking` subsection**, which MAY differ (it is the sweep axis for a benchmarking run). Any difference in `global`, or in any `services` subsection other than `benchmarking`, MUST still be rejected with an error naming the inconsistent field.

#### Scenario: Configs differing only in `services.benchmarking` load

- **WHEN** two configs are loaded that are identical except for `services.benchmarking.agent_md_file` and `services.benchmarking.name`
- **THEN** both configs load without error
- **AND** the loader holds two configs

#### Scenario: Differing `global` is still rejected

- **WHEN** two configs differ in their `global` section
- **THEN** the loader raises an error stating the `global` field must be consistent across all configurations

#### Scenario: Differing non-benchmarking `services` is still rejected

- **WHEN** two configs differ in a `services` subsection other than `benchmarking` (for example `services.chat_app`)
- **THEN** the loader raises an error stating the `services` field must be consistent across all configurations

#### Scenario: Identical configs still load (backward compatibility)

- **WHEN** two byte-identical configs are loaded
- **THEN** both load without error, exactly as before this change

### Requirement: `archi create` is unaffected by the benchmarking exemption

The exemption of `services.benchmarking` from the cross-config consistency check MUST NOT change `archi create` behavior. `archi create` does not consume `services.benchmarking`, so deployments created from configs that differ only in that subsection MUST be identical to those produced before this change.

#### Scenario: Create path unchanged

- **WHEN** `archi create` loads its configuration
- **THEN** the rendered deployment is identical to the pre-change behavior for the same input
- **AND** no `services.benchmarking` value influences the created deployment

### Requirement: Distinct rendered config files per variant

When more than one config is loaded for a deployment, the deployment manager SHALL render each config to a distinct file in the deployment's `configs/` directory, so the benchmarker iterates every variant. A single-config deployment SHALL still render to `config.yaml`. For multi-config runs the filename SHALL be derived from the per-variant `services.benchmarking.name` when present (otherwise the top-level `name`), with collisions disambiguated by the config index.

#### Scenario: Single-config renders config.yaml

- **WHEN** exactly one config is rendered
- **THEN** the output filename is `config.yaml`

#### Scenario: Multi-config renders one distinct file per variant

- **WHEN** three configs sharing the same top-level `name` but distinct `services.benchmarking.name` are rendered
- **THEN** three distinct files are written (named from each `services.benchmarking.name`)
- **AND** no config overwrites another

#### Scenario: Filename collision is disambiguated

- **WHEN** two configs would resolve to the same filename
- **THEN** the second is suffixed with its config index so both files persist

### Requirement: config-seed tolerates multi-config deployments

The config-seed step SHALL NOT abort a deployment when the rendered config is not named `config.yaml`. When the configured path is absent, it MUST fall back to the first `*.yaml` in the rendered-config directory. If no YAML is found, it MUST surface a clear file-not-found error.

#### Scenario: Per-variant filenames do not abort seeding

- **WHEN** the rendered-config directory contains per-variant files but no `config.yaml`
- **THEN** config-seed loads the first `*.yaml` and completes successfully
- **AND** the deployment proceeds to run the benchmark

#### Scenario: Single-config seeding unchanged

- **WHEN** the rendered-config directory contains `config.yaml`
- **THEN** config-seed loads `config.yaml` exactly as before
