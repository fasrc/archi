## ADDED Requirements

### Requirement: The config render SHALL keep the type of a configured parent_chunk_size and child_chunk_size
When `data_manager.chunking.parent_chunk_size` or `data_manager.chunking.child_chunk_size` is set, the rendered runtime config MUST hold the configured value with its configured type after `yaml.safe_load`. A string value MUST stay a string, also when its text is a number or a YAML 1.1 special word (`null`, `~`, `on`, `off`, `yes`, `no`, `true`). An integer value MUST stay an integer. When a size is not set, its key MUST be absent from the rendered config, so that ingestion uses the built-in default.

#### Scenario: A quoted number stays a string
- **WHEN** the config sets `parent_chunk_size` to the string `"2048"` and `child_chunk_size` to the string `"512"`
- **THEN** the rendered config, loaded with `yaml.safe_load`, has `parent_chunk_size` equal to the string `"2048"` and `child_chunk_size` equal to the string `"512"`

#### Scenario: A YAML special word stays a string
- **WHEN** the config sets a chunk size to the string `"null"`, `"~"`, `"on"`, or `"true"`
- **THEN** the rendered config holds that same string, not `None` and not a boolean

#### Scenario: An integer stays an integer
- **WHEN** the config sets `parent_chunk_size` to `1024` and `child_chunk_size` to `256`
- **THEN** the rendered config holds the integers `1024` and `256`

#### Scenario: An unset size stays absent
- **WHEN** the config sets only `child_chunk_size`
- **THEN** the rendered chunking section has no `parent_chunk_size` key
