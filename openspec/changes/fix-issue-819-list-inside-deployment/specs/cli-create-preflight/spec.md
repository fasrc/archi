## ADDED Requirements

### Requirement: A configured input list inside the deployment directory SHALL stop a forced create or evaluate before the teardown
`archi create --force` and `archi evaluate --force` MUST refuse to run when an entry of `data_manager.sources.links.input_lists` in any loaded config resolves to a path inside the deployment directory `<ARCHI_DIR>/archi-<name>/` (default `~/.archi/archi-<name>/`), the directory that `remove_existing_deployment()` deletes.
Both sides MUST be resolved as `Path.resolve()` resolves them: symbolic links followed, `..` collapsed, a relative path taken from the current directory (the rule of #617). `~` MUST NOT be expanded, as no other input-list path handling expands it.

The refusal MUST happen before `remove_existing_deployment()` and before any other change to the host. The command MUST exit with a non-zero status. The error MUST name every such list as configured (and its resolved path when the two differ), the resolved deployment directory, and the config file that lists it.

Without `--force` the check MUST NOT run: there is no teardown, and the existing-deployment refusal keeps its precedence. `archi restart` MUST NOT change. A list outside the directory MUST pass, including a list in a sibling directory whose name shares the prefix (`archi-<name>-2` next to `archi-<name>`). An entry that is not a string or a path object MUST NOT be refused by this check.

#### Scenario: Forced create with a list inside the deployment directory
- **WHEN** `archi create --force` runs against an existing deployment with a config whose `input_lists` holds the absolute path of a file under that deployment's directory (for example `weblists/x.list`)
- **THEN** the command exits non-zero
- **AND** `remove_existing_deployment()` and `delete_deployment()` are never called
- **AND** the listed file and the rest of the deployment directory are left intact
- **AND** the error names the path, the resolved deployment directory, and the config file

#### Scenario: Dry forced create with a list inside the deployment directory
- **WHEN** `archi create --force --dry` runs with the same config
- **THEN** the command exits non-zero with the same error as a real run
- **AND** the "would remove existing deployment" notice is not printed, because a real run would have refused before the teardown

#### Scenario: Forced create with a relative path that resolves inside the deployment directory
- **WHEN** `archi create --force` runs from a directory `C` with a config whose `input_lists` holds a relative path with `..` segments that resolves from `C` to a file under the deployment directory
- **THEN** the command exits non-zero before `remove_existing_deployment()`
- **AND** the error names the relative path as configured and its resolved path

#### Scenario: A symbolic link outside the directory that resolves inside it is refused
- **WHEN** an `input_lists` entry names a symbolic link outside the deployment directory whose target is a file inside it
- **THEN** the check refuses the entry
- **AND** the error names the link as configured and its resolved target

#### Scenario: Forced evaluate with a list inside the runtime directory
- **WHEN** `archi evaluate --force` runs against an existing benchmarking runtime with a config whose `input_lists` holds the absolute path of a file under that runtime's directory
- **THEN** the command exits non-zero
- **AND** `remove_existing_deployment()` and `delete_deployment()` are never called
- **AND** the listed file and the rest of the runtime directory are left intact

#### Scenario: Evaluate without --force reports the existing runtime, not the list
- **WHEN** `archi evaluate` runs without `--force` against an existing runtime with a config whose `input_lists` holds a file under that runtime's directory
- **THEN** the command exits non-zero with the "already exists" refusal, exactly as it does today
- **AND** the error does not mention the deployment-directory check
- **AND** the listed file is left intact

#### Scenario: Lists outside the deployment directory still pass
- **WHEN** every `input_lists` entry resolves to a path outside the deployment directory, by an absolute path or by a relative path from the current directory
- **THEN** the check passes and the command continues to its next step

#### Scenario: A sibling directory with the same prefix is outside
- **WHEN** an `input_lists` entry names a file under `<ARCHI_DIR>/archi-<name>-2/` while the deployment is `<name>`
- **THEN** the check passes

#### Scenario: Every list inside the directory in every config is named once
- **WHEN** two loaded configs list two distinct paths inside the deployment directory, one of them twice, and one path outside it
- **THEN** one error names each of the two inside paths once, each with its own config file, in configuration order
- **AND** the error does not name the path outside

#### Scenario: Entries and shapes that are not paths are ignored
- **WHEN** a config has no `data_manager`, or a `data_manager`, `sources`, or `links` value that is not a mapping, or an `input_lists` value that is not a list, or an `input_lists` entry that is not a string or a path object
- **THEN** the check yields nothing for that config or entry and passes
