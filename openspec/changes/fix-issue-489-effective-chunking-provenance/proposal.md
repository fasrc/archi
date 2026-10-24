## Why

Three surfaces report the top-level `data_manager.chunk_size` / `chunk_overlap` (defaults
1000 / 150) as the corpus chunking. Those two values feed only the `CharacterTextSplitter`
(`src/data_manager/vectorstore/manager.py:156-159`). The default ingest path is hierarchical
(`chunking.strategy` `sentence`, also when the key is unset, `manager.py:55-64`,
`:167-172`), and it uses `chunking.parent_chunk_size` (default 2048),
`chunking.child_chunk_size` (default 512), and `chunking.chunk_overlap` (default 20,
clamped) (`manager.py:42-81`, `src/data_manager/vectorstore/node_parsing.py:51-57`,
`:241-247`, `:264-266`, `:523-526`). The docs already say the top-level pair is
character-splitter-only (`docs/docs/configuration.md`). Only the reporting is wrong:

- the benchmark artifact's `config_version.key_settings`
  (`src/utils/benchmark_provenance.py:118-138`, `:779`);
- `GET /config/static` (`src/interfaces/chat_app/api.py:369-404`, fields at `:393-394`);
- the status board's ingest snapshot (`src/utils/ingest_provenance.py:24-35`, `:94-95`),
  consumed at `src/data_manager/vectorstore/manager.py:405` and
  `src/interfaces/chat_app/status_provenance.py:225`.

A reader of any of the three sees "1000 / 150" for a corpus built with 2048 / 512 / 20.

Issue #489. Operator decision 2026-09-26: option A — add an `effective_chunking` block to
`build_ingest_config_snapshot` and consume it on all three surfaces.

### Corrections to the issue body (verified on origin/dev `db701852`)

1. **Path selection.** The issue's acceptance criterion 2 says
   `hierarchical_rerank.enabled: false` selects the character path. The code does not do
   that: the ingest path is chosen by `data_manager.chunking.strategy` alone
   (`manager.py:169-172`: hierarchical when the strategy is `sentence` or `markdown`,
   otherwise the `CharacterTextSplitter`). `hierarchical_rerank` is a retriever setting
   (`src/data_manager/vectorstore/retrievers/factory.py:53`). This change mirrors the code,
   which is the issue's own objective ("the parameters the hierarchical ingest path
   actually uses"). The criterion is restated with `chunking.strategy: character`.
2. **Overlap.** #403 is closed and merged: `chunking.chunk_overlap` is configurable
   (`manager.py:67-81`) and the snapshot already carries `child_chunk_overlap`
   (unclamped, `ingest_provenance.py:84-89`). The issue's "if #403 lands first, the
   configured value" branch applies. `effective_chunking` reports the clamped value the
   node parser applies.
3. **Anchors moved.** Line numbers in the issue body are from `0ddc96e1`; this proposal
   uses `db701852`. `data_manager.chunking` is already in `KEY_SETTING_PATHS` (`:134`), but
   it omits absent keys and every default, so it does not report the effective values.

## What Changes

- `src/utils/ingest_provenance.py`: add a pure public helper
  `effective_chunking(data_manager_config)` and an `effective_chunking` key in
  `INGEST_CONFIG_KEYS` and in `build_ingest_config_snapshot`. The existing `chunk_size`,
  `chunk_overlap`, and `child_chunk_overlap` keys stay unchanged.
- `src/utils/benchmark_provenance.py`: the live stamp's `key_settings` carries
  `effective_chunking` beside the existing top-level pair.
- `GET /config/static`: add an `effective_chunking` field, computed from the stored
  `data_manager_config` through the helper. `api.py` gets a one-line call only. No
  Postgres schema change.
- Status board: the snapshot and the drift comparison carry the new key with no change to
  `status_provenance.py`. An older snapshot without the key reports no drift for it
  (`compare_ingest_config` already skips a declared key absent from `at_ingest`).

## Capabilities

### New Capabilities
- `ingest-chunking-provenance`: every surface that reports corpus chunking reports the
  values the ingest path applies.

### Modified Capabilities
None.

## Impact

- Code: `src/utils/ingest_provenance.py`, `src/utils/benchmark_provenance.py`,
  `src/interfaces/chat_app/api.py` (one call site).
- Tests: `tests/unit/test_ingest_provenance.py`, the benchmark provenance tests.
- Out of scope: making overlap configurable (done in #403), the `static_config` schema,
  and the meaning of the top-level `chunk_size` / `chunk_overlap`.
