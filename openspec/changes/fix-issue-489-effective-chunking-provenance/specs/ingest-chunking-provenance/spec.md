## ADDED Requirements

### Requirement: The ingest snapshot SHALL report the effective chunking parameters
`build_ingest_config_snapshot` MUST emit an `effective_chunking` mapping computed by the public helper `effective_chunking(data_manager_config)` in `src/utils/ingest_provenance.py`. The path MUST be `"hierarchical"` when `data_manager.chunking.strategy` (default `"sentence"`) is `"sentence"` or `"markdown"`, and `"character"` otherwise. A hierarchical result MUST carry `path`, `strategy`, `parent_chunk_size` (default 2048), `child_chunk_size` (default 512), and `child_chunk_overlap` clamped as the node parser clamps it for that strategy. A character result MUST carry `path`, `strategy`, and the top-level `chunk_size` (default 1000) and `chunk_overlap` (default 150). The existing `chunk_size`, `chunk_overlap`, and `child_chunk_overlap` snapshot keys MUST keep their current values. Under the `markdown` strategy the result MUST also carry `non_markdown_child_chunk_overlap`, the overlap clamped as the sentence parser clamps it, because every non-Markdown file falls back to the sentence parser. The helper MUST NOT raise for any input.

#### Scenario: Hierarchical config with no chunking block
- **WHEN** the snapshot is built from a `data_manager` config with no `chunking:` block
- **THEN** `effective_chunking` equals `{"path": "hierarchical", "strategy": "sentence", "parent_chunk_size": 2048, "child_chunk_size": 512, "child_chunk_overlap": 20}`
- **AND** `chunk_size` is 1000 and `chunk_overlap` is 150

#### Scenario: Character strategy
- **WHEN** the config sets `chunking.strategy: character`, `chunk_size: 800`, and `chunk_overlap: 100`
- **THEN** `effective_chunking` equals `{"path": "character", "strategy": "character", "chunk_size": 800, "chunk_overlap": 100}`

#### Scenario: Unknown strategy uses the character path
- **WHEN** the config sets `chunking.strategy` to a value other than `sentence` or `markdown`
- **THEN** `effective_chunking["path"]` is `"character"`

#### Scenario: Overlap is clamped as the node parser clamps it
- **WHEN** the config sets `chunking.strategy: sentence`, `parent_chunk_size: 30`, `child_chunk_size: 512`, and `chunk_overlap: 40`
- **THEN** `effective_chunking["child_chunk_overlap"]` is 15
- **AND** with `strategy: markdown` and the same sizes it is 40
- **AND** with `strategy: markdown` and the same sizes `non_markdown_child_chunk_overlap` is 15

#### Scenario: Invalid values do not raise
- **WHEN** a chunk size or the overlap is a bool, a string, or a negative number
- **THEN** the helper returns the configured value unclamped and does not raise

#### Scenario: Restated defaults match the node parser
- **WHEN** the unit tests import the node-parser defaults, strategy names, and `_clamped_overlap`
- **THEN** the values restated in `ingest_provenance.py` equal them

### Requirement: Drift against an older snapshot SHALL NOT report effective_chunking
`compare_ingest_config` MUST report `effective_chunking` as drift only when both snapshots carry it and the values differ.

#### Scenario: Old snapshot without the key
- **WHEN** `compare_ingest_config(current, at_ingest)` runs with an `at_ingest` snapshot that has no `effective_chunking` key
- **THEN** no drift entry names `effective_chunking`

#### Scenario: Both sides carry different values
- **WHEN** both snapshots carry `effective_chunking` and the child chunk size differs
- **THEN** one drift entry names `effective_chunking` with both values

### Requirement: The benchmark stamp SHALL carry effective_chunking in key_settings
The live benchmark version stamp MUST include `key_settings["effective_chunking"]` computed by the same helper from the basis config's `data_manager` section, beside the existing `data_manager.chunk_size` and `data_manager.chunk_overlap` entries. When the basis has no `data_manager` mapping, the key MUST be absent. The reconstructed stamp for an old artifact MUST NOT add the key.

#### Scenario: Live stamp with a data_manager section
- **WHEN** the live stamp is built from a config with a `data_manager` section
- **THEN** `key_settings["effective_chunking"]` equals `effective_chunking(config["data_manager"])`

#### Scenario: Live stamp without a data_manager section
- **WHEN** the live stamp is built from a config with no `data_manager` section
- **THEN** `key_settings` has no `effective_chunking` key

### Requirement: GET /config/static SHALL return effective_chunking
The `/config/static` response MUST include an `effective_chunking` field computed by the helper from the stored `data_manager_config`, with no change to the `static_config` table schema. The existing `chunk_size` and `chunk_overlap` fields MUST stay.

#### Scenario: Static config response
- **WHEN** a client requests `GET /config/static`
- **THEN** the response carries `effective_chunking` equal to `effective_chunking(static_config.data_manager_config)`
