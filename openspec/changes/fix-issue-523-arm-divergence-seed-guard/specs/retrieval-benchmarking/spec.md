## ADDED Requirements

### Requirement: The config seeder refuses arms that disagree outside the arm-override paths
The config seeder SHALL exit non-zero, before it connects to Postgres, when a multi-config deployment (no `config.yaml`, two or more `*.yaml` arm files) has arm files that differ at any dotted path outside `DIVERGENCE_IGNORED_PATHS` (`services.benchmarking`, the deploy-rewritten `agents_dir` and `skills_dir` paths, and `name`). The failure output SHALL name each differing file, the reference file, and each diverging dotted path. A single-config deployment and a multi-config deployment whose arms agree SHALL seed exactly as before.

#### Scenario: Arms differ only under services.benchmarking
- **WHEN** two arm files differ only under `services.benchmarking`
- **THEN** the seeder seeds Postgres from the first file and exits 0

#### Scenario: Arms differ only in name or a deploy-rewritten path
- **WHEN** two arm files differ only in `name` or in `services.chat_app.agents_dir`
- **THEN** the seeder seeds Postgres from the first file and exits 0

#### Scenario: Arms differ in a chat_app setting
- **WHEN** two arm files differ in `services.chat_app.force_initial_retrieval`
- **THEN** the seeder exits non-zero, the output names `services.chat_app.force_initial_retrieval` and both files, and no call reaches the config service

#### Scenario: A key is present in only one arm file
- **WHEN** one arm file sets a key with a non-null value and the other arm file does not have that key
- **THEN** the seeder reports that dotted path as a difference and exits non-zero

#### Scenario: config.yaml is present
- **WHEN** the rendered-config directory contains `config.yaml` and other `*.yaml` files that differ from it
- **THEN** the seeder seeds from `config.yaml` without a divergence check
