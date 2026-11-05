## Why

Since #596, `src/cli/templates/base-config.yaml` renders `parent_chunk_size` and
`child_chunk_size` with `| tojson` (`base-config.yaml:359-364` on `origin/dev`
`5f3d80e9`). A quoted size such as `"512"` now stays a string in the runtime config.
Before #596, YAML silently turned it into the integer `512`.

`_resolve_chunk_sizes` (`src/data_manager/vectorstore/manager.py:53-63`) returns both
sizes with no type check. So a bad size fails late, once per document, deep in the
parser:

- A string: the `sentence` path fails in `_clamped_overlap` with `TypeError: unsupported
  operand type(s) for //: 'str' and 'int'`. The `markdown` path fails at the token-count
  comparison in `node_parsing.py:395`.
- A bool: `parent_chunk_size: true` passes as the size 1 (`True == 1`), and `false` as 0.
- An empty key (`parent_chunk_size:`): `None` reaches the parser, which fails on
  `None // 2`.

`_resolve_chunk_overlap` (`manager.py:78-93`) already refuses a bad `chunk_overlap` with one
`ValueError` when the manager is built. The two sizes need the same rule (issue #778).

`effective_chunking` (`src/utils/ingest_provenance.py:90`) reads the sizes with
`chunking.get(key, default)`, so an empty key is reported as `None`, not as the default
that the manager will apply.

## What Changes

- A new helper `_resolve_chunk_size(chunking_cfg, key, default)` in `manager.py`: `None`
  resolves to `default`; a bool, a non-`int`, or a value below 1 raises
  `ValueError(f"data_manager.chunking.{key} must be a positive int, got {value!r}")`.
  `_resolve_chunk_sizes` calls it once for each key. The call site in
  `VectorStoreManager.__init__` (`manager.py:186-188`) does not change.
- `effective_chunking` maps a `None` size to its default, the same way it maps a `None`
  `chunk_overlap` (`ingest_provenance.py:108-110`). It still never raises.
- `docs/docs/configuration.md`: the backward-compatibility note says that a size that is
  not an integer of 1 or more is refused with an error that names the key, and that an
  empty key uses the default. The #596 sentence "the hierarchical parser cannot use it" is
  replaced.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `hierarchical-rerank-retrieval`: one added requirement. The manager refuses a chunk size
  that is not a positive integer, and resolves an empty one to its default. It uses
  `## ADDED Requirements` because the #403 and #596 deltas for this capability are not
  archived yet.
- `ingest-chunking-provenance`: one added requirement. `effective_chunking` reports the
  default for an empty size. It uses `## ADDED Requirements` because the capability exists
  only in the unarchived #489 change.

## Impact

- Code: `src/data_manager/vectorstore/manager.py` (one new helper, `_resolve_chunk_sizes`
  body and docstring), `src/utils/ingest_provenance.py` (4 lines in `effective_chunking`).
- Tests: `tests/unit/test_vectorstore_manager_hierarchical.py` (4 new test functions, 15
  test items, appended at the end), `tests/unit/test_ingest_provenance.py` (3 new test
  functions, 4 test items, appended at the end).
- Docs: `docs/docs/configuration.md` (one note in the `chunking` section).
- Runtime: the data manager and the chat service both build `VectorStoreManager`
  (`src/data_manager/data_manager.py:51`; `ChatWrapper` at `app.py:458`, built in
  `FlaskAppWrapper.__init__` at `app.py:2841`). With a bad size, both now stop at startup
  with one clear error. Today they start, and then a string or empty size fails each
  document at ingest, and a bool size silently gives a size of 1 or 0. A bad
  `chunk_overlap` already stops them the same way.
- Not changed: `src/cli/templates/base-config.yaml` (#596 owns it) and the
  `src/interfaces/chat_app/app.py` code.
