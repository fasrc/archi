## ADDED Requirements

### Requirement: A configured input list path that does not exist SHALL stop create, restart, and evaluate before any change
`archi create`, `archi restart --config`, and `archi evaluate` MUST refuse to run when an entry of `data_manager.sources.links.input_lists` in any loaded config names a path that does not exist (`os.path.exists` is false).
The refusal MUST happen before `ConfigurationManager.set_sources_enabled()` writes any `enabled` key and before any `--force` teardown. The command MUST exit with a non-zero status. The error MUST name every missing path, the config file that lists it, and the current directory.

A relative path MUST keep resolving from the current directory, not from the directory of the config file. A path that exists but is not a readable file MUST NOT be refused by this check: `get_enabled_sources()` and staging keep their warn-and-skip behavior for it (#636). An entry that is not a string or a path object MUST NOT be refused by this check.

#### Scenario: Forced create with a missing relative list
- **WHEN** `archi create --force` runs against an existing deployment with a config whose `input_lists` holds a relative path that does not exist from the current directory
- **THEN** the command exits non-zero
- **AND** neither `remove_existing_deployment()` nor `set_sources_enabled()` is called
- **AND** the existing deployment directory and its contents are left intact
- **AND** the error names the path, the config file, and the current directory

#### Scenario: Dry-run create with a missing list
- **WHEN** `archi create --force --dry` runs with a config whose `input_lists` holds a path that does not exist
- **THEN** the command exits non-zero with the same error as a real run

#### Scenario: Restart with a missing list
- **WHEN** `archi restart --config` runs with a config whose `input_lists` holds a path that does not exist
- **THEN** the command exits non-zero and prints the error, not a traceback
- **AND** `set_sources_enabled()` is not called, no deployment file is rendered again, and no service is restarted

#### Scenario: Forced evaluate with a missing list
- **WHEN** `archi evaluate --force` runs against an existing runtime with a config whose `input_lists` holds a path that does not exist
- **THEN** the command exits non-zero
- **AND** neither `remove_existing_deployment()` nor `set_sources_enabled()` is called
- **AND** the existing runtime directory and its contents are left intact

#### Scenario: Every missing path in every config is named
- **WHEN** two loaded configs list three paths that do not exist and one path that exists
- **THEN** one error names each of the three missing paths with its own config file
- **AND** the error does not name the path that exists

#### Scenario: A relative path does not resolve against the config directory
- **WHEN** a config file in directory `D` lists the relative path `x.list`, the file `D/x.list` exists, and the current directory has no `x.list`
- **THEN** the check refuses `x.list`

#### Scenario: An existing list still passes
- **WHEN** every `input_lists` entry names an existing file, by a relative path from the current directory or by an absolute path
- **THEN** the check passes and the command continues to its next step

#### Scenario: An existing list that cannot be read still warns and is skipped
- **WHEN** an `input_lists` entry names an existing file that cannot be read or decoded as UTF-8
- **THEN** the check passes
- **AND** `get_enabled_sources()` logs a WARNING that names the file and skips it
