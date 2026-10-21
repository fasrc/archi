## ADDED Requirements

### Requirement: The input-list prefix map SHALL have exactly one definition
The mapping from an `input_lists` entry prefix (`git-`, `sso-`, `elog-`, `indico-`) to its source name SHALL be defined once, in `src/cli/source_registry.py`, and the scraper, the sources builder, and the golden-set maintenance code SHALL derive their prefixes from it.

#### Scenario: One literal site
- **WHEN** a reader runs `grep -rn '"git-"' src/`
- **THEN** the command returns exactly one line, in `src/cli/source_registry.py`

#### Scenario: Scraper classification is unchanged for enabled sources
- **WHEN** the scraper splits a list that has `git-`, `sso-`, `sitemap-`, `elog-`, `indico-`, ELOG-path, and plain URLs
- **THEN** each URL lands in the same bucket, with the same prefix stripped, as before this change

### Requirement: An explicit `enabled: false` SHALL win over input-list entries in the scraper
When `data_manager.sources.<source>.enabled` is explicitly `false` (or the section is the boolean `false`) for `git`, `sso`, `elog`, or `indico`, the scraper SHALL NOT collect that source's `input_lists` entries and SHALL log exactly one WARNING that names the source and the number of skipped entries.

#### Scenario: Disabled git with git- entries
- **WHEN** `git.enabled` is `false` and the input lists hold two `git-` entries
- **THEN** no git URL is passed to the git collector, and one WARNING contains `git` and `2`

#### Scenario: Same rule for sso, elog, and indico
- **WHEN** `sso.enabled`, `elog.enabled`, or `indico.enabled` is `false` and the lists hold entries for that source
- **THEN** those entries are not collected, and one WARNING names the source and the count

#### Scenario: Scheduled ELOG re-collection obeys explicit false
- **WHEN** `schedule_collect_elog` runs and `elog.enabled` is `false`
- **THEN** no catalog ELOG URL is collected, and a WARNING is logged

### Requirement: An absent or true `enabled` key SHALL let input-list entries be collected
When the source's `enabled` key is absent (or `null`), list entries for that source SHALL enable it for the run, with one INFO line naming the source and the count. When the key is `true`, the entries SHALL be collected.

#### Scenario: Absent key with git- entries
- **WHEN** the config has no `git.enabled` key and the lists hold a `git-` entry
- **THEN** the git collector receives the URL

#### Scenario: True key with entries
- **WHEN** `git.enabled` is `true` and the lists hold a `git-` entry
- **THEN** the git collector receives the URL

### Requirement: The CLI SHALL infer a source from input-list entries when its `enabled` key is absent
`ConfigurationManager.get_enabled_sources` SHALL read each configured input-list file and include a registered source when an entry for that source is present (by prefix, or for `elog` by the ELOG path heuristic) and that source's `enabled` key is absent. When the key is explicitly `false`, the source SHALL stay out and the CLI SHALL log a warning with the entry count. A configured list path that is not a readable file SHALL be warned about and skipped, not raised.

#### Scenario: Inference adds git and its secrets
- **WHEN** a list file has a `git-` entry and `git.enabled` is absent
- **THEN** `get_enabled_sources()` includes `git`, and `source_registry.required_secrets` for the result includes `GIT_USERNAME` and `GIT_TOKEN`

#### Scenario: Explicit false excludes git
- **WHEN** a list file has a `git-` entry and `git.enabled` is `false`
- **THEN** `get_enabled_sources()` does not include `git`, and a warning names `git` and the count

#### Scenario: Missing list file
- **WHEN** a configured input-list path does not exist
- **THEN** `get_enabled_sources()` returns without raising and logs a warning that names the path

#### Scenario: ELOG list entry survives a CLI deploy
- **WHEN** a list file has an `elog-` entry, `elog.enabled` is absent, and the CLI runs `get_enabled_sources` then `set_sources_enabled`
- **THEN** the config holds `elog.enabled: true`
