## ADDED Requirements

### Requirement: effective_chunking SHALL report the default for a chunk size that is present with no value
On the hierarchical path (`sentence` or `markdown`), when `parent_chunk_size` or `child_chunk_size` is `None`, `effective_chunking` MUST report that key's built-in default (2048 or 512), which is the size the vectorstore manager applies, and MUST clamp the reported overlap with that default. It MUST still never raise, and it MUST still report a size that the manager refuses (for example a string) unclamped.

#### Scenario: Empty sizes report the defaults
- **WHEN** `effective_chunking` reads `{"chunking": {"strategy": "sentence", "parent_chunk_size": None, "child_chunk_size": None}}`, or the same with `markdown`
- **THEN** it reports `parent_chunk_size` 2048 and `child_chunk_size` 512

#### Scenario: The overlap is clamped with the default parent size
- **WHEN** the strategy is `sentence`, `parent_chunk_size` is `None`, and `child_chunk_size` is 30
- **THEN** it reports `child_chunk_overlap` 15, the overlap that the manager applies

#### Scenario: A string size is still reported without an error
- **WHEN** `parent_chunk_size` is the string `"nope"`
- **THEN** `effective_chunking` returns without raising and reports the overlap unclamped
