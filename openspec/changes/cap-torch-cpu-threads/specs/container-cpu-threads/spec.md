## ADDED Requirements

### Requirement: Embedding containers run torch with a bounded CPU thread count
The rendered compose file SHALL set `OMP_NUM_THREADS` in the environment of the `data-manager` and `chatbot` services. Its value SHALL be `data_manager.torch_threads` when configured, and 8 otherwise. No other service SHALL receive the variable from this setting.

#### Scenario: Default when the key is absent
- **WHEN** a deployment is created from a config with no `data_manager.torch_threads`
- **THEN** the rendered `data-manager` and `chatbot` services both carry `OMP_NUM_THREADS: "8"`

#### Scenario: Operator override
- **WHEN** a deployment is created with `data_manager.torch_threads: 4`
- **THEN** the rendered `data-manager` and `chatbot` services both carry `OMP_NUM_THREADS: "4"`

#### Scenario: Other services are untouched
- **WHEN** a deployment that also enables grader, slack or benchmarking is rendered
- **THEN** those services carry no `OMP_NUM_THREADS` from this setting

### Requirement: An invalid thread count is refused at create time
`archi create` SHALL refuse a `data_manager.torch_threads` value that is not a positive integer: zero, a negative number, a boolean, a float or a string. The error message SHALL name `data_manager.torch_threads`.

#### Scenario: Zero is refused
- **WHEN** `data_manager.torch_threads` is `0`
- **THEN** rendering fails with an error naming `data_manager.torch_threads`, and no compose file is written with that value

#### Scenario: A string is refused
- **WHEN** `data_manager.torch_threads` is `"8"`
- **THEN** rendering fails with an error naming `data_manager.torch_threads`
