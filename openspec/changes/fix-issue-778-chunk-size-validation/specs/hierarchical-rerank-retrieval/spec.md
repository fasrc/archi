## ADDED Requirements

### Requirement: The vectorstore manager SHALL refuse a parent_chunk_size or child_chunk_size that is not a positive integer
When `data_manager.chunking.parent_chunk_size` or `data_manager.chunking.child_chunk_size` is a bool, is not an `int`, or is below 1, building the vectorstore manager MUST raise `ValueError`. The message MUST name the key as `data_manager.chunking.<key>` and MUST end with the bad value in `repr` form. A key that is present with no value (`None`) MUST resolve to its built-in default (2048 for the parent, 512 for the child), the same as an absent key. A positive `int`, 1 included, MUST be used as given.

#### Scenario: A quoted size is refused when the manager is built
- **WHEN** `data_manager.chunking.child_chunk_size` is the string `"512"`
- **THEN** constructing `VectorStoreManager` raises `ValueError` whose message contains `data_manager.chunking.child_chunk_size`, before any document is parsed

#### Scenario: Each bad value is refused for each key
- **WHEN** `parent_chunk_size` or `child_chunk_size` is `"512"`, `True`, `False`, `0`, `-1`, or `2.5`
- **THEN** `_resolve_chunk_sizes` raises `ValueError` that names that key and ends with the value's `repr`

#### Scenario: An empty value uses the default
- **WHEN** `parent_chunk_size` and `child_chunk_size` are both `None`
- **THEN** `_resolve_chunk_sizes` returns `(2048, 512)`, and each key falls back on its own when only one is `None`

#### Scenario: Valid sizes are unchanged
- **WHEN** the keys are absent, or are `1024` and `256`, or are both `1`
- **THEN** `_resolve_chunk_sizes` returns `(2048, 512)`, `(1024, 256)`, and `(1, 1)`
