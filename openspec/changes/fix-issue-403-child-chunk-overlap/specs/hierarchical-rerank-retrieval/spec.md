## ADDED Requirements

### Requirement: Configurable child-chunk overlap
The hierarchical chunkers SHALL read the child-splitter overlap from `data_manager.chunking.chunk_overlap`, defaulting to 20 when the key is absent or empty.
The overlap a child splitter receives SHALL be `min(configured, size // 2)`, where `size` is `child_chunk_size` on the `markdown` path and `min(parent_chunk_size, child_chunk_size)` on the `sentence` path. The `markdown` parent splitter SHALL keep overlap `0`. A configured value that is a bool, is not an integer, or is negative SHALL fail at vectorstore-manager construction with an error that names the key. The CLI template SHALL emit the key only when it is set, and a configured `0` SHALL render as `0`.

#### Scenario: Absent key keeps the current overlap
- **WHEN** `data_manager.chunking.chunk_overlap` is absent and `child_chunk_size` is 512
- **THEN** each child splitter receives overlap 20, as before this change

#### Scenario: Zero overlap is honored end to end
- **WHEN** `data_manager.chunking.chunk_overlap` is `0`
- **THEN** the rendered config carries `chunk_overlap: 0` and each child splitter receives overlap 0

#### Scenario: A large overlap is clamped to half the child size
- **WHEN** `chunk_overlap` is 500 and `child_chunk_size` is 512 on the `markdown` path
- **THEN** the child splitter receives overlap 256

#### Scenario: The markdown parent splitter keeps no overlap
- **WHEN** any `chunk_overlap` is configured on the `markdown` path
- **THEN** the parent splitter receives overlap 0

#### Scenario: An invalid value fails at startup
- **WHEN** `chunk_overlap` is `-1`, `true`, `"20"`, or `2.5`
- **THEN** constructing the vectorstore manager raises an error that names `data_manager.chunking.chunk_overlap`

#### Scenario: The measurement script reports the overlap the ingest uses
- **WHEN** the chunk-overlap measurement script computes an effective overlap for a size pair and a requested overlap
- **THEN** the value equals the overlap the production child splitter receives for the same inputs
