## ADDED Requirements

### Requirement: The judge timeout validator never raises on a numeric value
The `timeout` validator SHALL treat a number that cannot convert to a float as invalid, fall back to the default `180`, and log a warning, without raising an exception.

#### Scenario: Oversized integer timeout
- **WHEN** `ragas_run_config_kwargs({"timeout": 10**309, "max_workers": 10**309})` is called
- **THEN** it returns without an exception, `timeout` equals `180`, and `max_workers` equals `10**309`

### Requirement: An integral timeout has one canonical form
The `timeout` validator SHALL return an integral float as an `int`, and SHALL return a non-integral float unchanged.

#### Scenario: 600 and 600.0 are the same setting
- **WHEN** `ragas_effective_settings({"timeout": 600})` and `ragas_effective_settings({"timeout": 600.0})` are called
- **THEN** the two results are equal and both `timeout` values are the `int` `600`

#### Scenario: A fractional timeout stays fractional
- **WHEN** `ragas_effective_settings({"timeout": 120.5})` is called
- **THEN** the result `timeout` equals `120.5`

### Requirement: The selected-file digest fingerprints the file as written
The run record's `config_version.selected_file_digest` SHALL be computed from the selected configuration file as parsed, before any prompt path is replaced by the contents of its file.

#### Scenario: Different prompt paths with identical contents
- **WHEN** two selected files differ only in the paths under `services.benchmarking.prompts`, and the two prompt files have identical contents
- **THEN** the two run records have different `selected_file_digest` values

#### Scenario: Prompt file contents change
- **WHEN** the same selected file is reported twice and the contents of a referenced prompt file change between the two reports
- **THEN** the two run records have the same `selected_file_digest`

#### Scenario: A file without prompts keeps its digest
- **WHEN** the selected file has no `services.benchmarking.prompts` key
- **THEN** `selected_file_digest` equals the digest `config_version` gives for the parsed file passed directly as `selected`

### Requirement: The timeout fallback is documented
`docs/docs/benchmarking.md` SHALL state that `mode_settings.ragas_settings.timeout` must be a positive number and that an invalid value falls back to `180` with a warning.

#### Scenario: Operator reads the timeout row
- **WHEN** an operator reads the `timeout` row of the benchmarking configuration table
- **THEN** the row names the invalid values (`0`, `-1`, a non-number, NaN, infinity, an oversized integer) and the `180` fallback with a warning
