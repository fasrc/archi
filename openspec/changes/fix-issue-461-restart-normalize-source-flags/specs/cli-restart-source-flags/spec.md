## ADDED Requirements

### Requirement: archi restart normalizes source enabled flags before it validates or compares the new config
When `archi restart` receives `--config` or `--config-dir`, it SHALL call `ConfigurationManager.set_sources_enabled` with the resolved enabled sources before `validate_configs` and before `_validate_non_chatbot_sections`.

This makes the restart comparison see the same `enabled` flags that `archi create` wrote into
the deployed config. A config that `archi create` accepted, and that the operator did not
change, is then not refused by the restricted-section comparison.

#### Scenario: an omitted enabled key matches a deployed false
- **WHEN** the new config omits `data_manager.sources.git.enabled`, `git` is not a selected source, and the deployed rendered config has `data_manager.sources.git.enabled: false`
- **THEN** `archi restart` does not raise "Restart config changes are restricted to chatbot settings only"

#### Scenario: the normalization runs first
- **WHEN** `archi restart` runs with `--config`
- **THEN** `set_sources_enabled` is called before `validate_configs`
- **AND** `set_sources_enabled` is called before `_validate_non_chatbot_sections`

#### Scenario: a real data_manager change is still refused
- **WHEN** the new config sets `data_manager.sources.git.enabled: true` and the deployed rendered config has `data_manager.sources.git.enabled: false`
- **THEN** `archi restart` raises "Restart config changes are restricted to chatbot settings only"
