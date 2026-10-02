## ADDED Requirements

### Requirement: The replacement deployment SHALL render completely before any teardown
`archi create` and `archi evaluate` SHALL render every stage of the replacement deployment's template workflow into a temporary directory, and SHALL refuse the run with that stage's error before `remove_existing_deployment()` runs when any stage raises, so that a deterministic render failure never costs the operator the existing deployment.
The preflight runs the same stage list as the real render, so a stage added later is covered with no further change; it skips only the source copy (large, and unable to fail on config input) and the live port-availability probe (the existing deployment still holds its ports before the teardown, so a probe there refuses every re-create that reuses them). The pure port-configuration check still runs. The temporary directory is removed on success and on failure. The real render that follows the teardown writes the same files it would have written without the preflight. A failure that is not deterministic in the config — a port held by another process, a full disk, an image that will not start — can still occur after the teardown; `--force` is still not transactional past `compose up`.

#### Scenario: A late render stage refuses create --force before the teardown
- **WHEN** `archi create --force` targets an existing deployment and passes every earlier validation, and any stage of the template workflow raises
- **THEN** the command exits non-zero with that stage's error message
- **AND** `remove_existing_deployment()` performs no teardown and the existing deployment directory is unchanged

#### Scenario: A late render stage refuses evaluate --force before the teardown
- **WHEN** `archi evaluate --force` targets an existing benchmarking runtime and passes every earlier validation, and any stage of the template workflow raises
- **THEN** the command exits non-zero with that stage's error message
- **AND** no teardown runs and the existing runtime directory is unchanged

#### Scenario: Two benchmark configs whose agent_md_file share a basename refuse before the teardown
- **WHEN** `archi evaluate --force --config-dir` is given two configs whose `services.benchmarking.agent_md_file` values differ but share a basename
- **THEN** the command exits non-zero with the basename-collision error
- **AND** no teardown runs

#### Scenario: A missing agent_md_file refuses before the teardown
- **WHEN** `archi evaluate --force` is given a config whose `services.benchmarking.agent_md_file` does not exist
- **THEN** the command exits non-zero and the error names the missing file
- **AND** no teardown runs

#### Scenario: A dry run reports the failure a real run would hit
- **WHEN** `archi create --dry --force` is run and any stage of the template workflow raises
- **THEN** the command exits non-zero with that stage's error message
- **AND** no teardown runs, and no deployment directory or volume is created

#### Scenario: A re-create that reuses its own ports is not refused by the preflight
- **WHEN** the existing deployment holds the ports that the replacement reuses and `archi create --force` is run
- **THEN** the preflight does not probe port availability, so it does not refuse the run for those ports
- **AND** an invalid or duplicated port value is still refused before the teardown

#### Scenario: The preflight does not change the real render
- **WHEN** the replacement renders once without a preflight and once after a preflight on the same inputs
- **THEN** the two rendered trees are byte-identical, apart from the source copy
- **AND** the preflight's temporary directory no longer exists after it returns or raises
