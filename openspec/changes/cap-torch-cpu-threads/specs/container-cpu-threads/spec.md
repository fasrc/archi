## ADDED Requirements

### Requirement: Embedding containers run torch with a bounded CPU thread count
The rendered compose file SHALL bound torch's CPU threads in the `data-manager` and `chatbot` services, and in no other service. A configured `data_manager.torch_threads` SHALL be rendered into both as `OMP_NUM_THREADS`. Without it, an `OMP_NUM_THREADS` that the deployment's env file sets SHALL reach both containers unchanged, so compose SHALL render no `OMP_NUM_THREADS` entry that overrides it. Otherwise both SHALL carry 8, capped at the number of CPUs the process that renders the file can run on.

#### Scenario: Default when the key is absent
- **WHEN** a deployment is created from a config with no `data_manager.torch_threads`, on a host where `archi create` can run on 8 or more CPUs
- **THEN** the rendered `data-manager` and `chatbot` services both carry `OMP_NUM_THREADS: "8"`

#### Scenario: Default on a host with fewer than 8 CPUs
- **WHEN** a deployment is created from a config with no `data_manager.torch_threads`, on a host where `archi create` can run on 4 CPUs
- **THEN** the rendered `data-manager` and `chatbot` services both carry `OMP_NUM_THREADS: "4"`

#### Scenario: Operator override
- **WHEN** a deployment is created with `data_manager.torch_threads: 4`
- **THEN** the rendered `data-manager` and `chatbot` services both carry `OMP_NUM_THREADS: "4"`, whatever the CPU count and the env file

#### Scenario: The env file already sets the variable
- **WHEN** a deployment is created with no `data_manager.torch_threads` and an env file that sets `OMP_NUM_THREADS=4`
- **THEN** the rendered `data-manager` and `chatbot` services carry no `OMP_NUM_THREADS` entry, and both read 4 from the deployment's `.env`

#### Scenario: Other services are untouched
- **WHEN** a deployment that also enables grader, slack or benchmarking is rendered
- **THEN** those services carry no `OMP_NUM_THREADS` from this setting

### Requirement: An invalid thread count is refused before any file is written
`archi create` and `archi restart -c` SHALL refuse a `data_manager.torch_threads` value that is not a positive integer (zero, a negative number, a boolean, a float or a string) before either writes a deployment file. The error message SHALL name `data_manager.torch_threads`.

#### Scenario: Zero is refused
- **WHEN** `data_manager.torch_threads` is `0`
- **THEN** rendering fails with an error naming `data_manager.torch_threads`, and no compose file is written with that value

#### Scenario: A string is refused
- **WHEN** `data_manager.torch_threads` is `"8"`
- **THEN** rendering fails with an error naming `data_manager.torch_threads`

#### Scenario: Restart refuses an invalid value before it writes
- **WHEN** `archi restart -c` receives a config with `data_manager.torch_threads: 0`
- **THEN** it fails with an error naming `data_manager.torch_threads`, and the deployed config files are unchanged

### Requirement: Restart refuses a thread-count change
`archi restart -c` SHALL refuse a config whose `data_manager.torch_threads` differs from the deployed config's, the same way it refuses other `data_manager` changes, because restart recreates only the named service and the data-manager would keep the old count.

#### Scenario: A new thread count is refused
- **WHEN** a deployment created without `data_manager.torch_threads` is restarted with a config that sets it to 4
- **THEN** restart fails with the restricted-sections error naming `data_manager`

#### Scenario: An unchanged thread count passes
- **WHEN** a deployment created with `data_manager.torch_threads: 4` is restarted with a config that also sets 4
- **THEN** the restricted-sections check passes
