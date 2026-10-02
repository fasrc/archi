# Make the hierarchical child-chunk overlap configurable

## Why

The hierarchical chunkers (`sentence` and `markdown`) give every child splitter a fixed
overlap: the constant `CHILD_CHUNK_OVERLAP = 20`
(`src/data_manager/vectorstore/node_parsing.py:58`), clamped by `_clamped_overlap`
(`node_parsing.py:234-243`) to at most the chunk size. Nothing in the config can change it,
so the #396 feature matrix cannot run an overlap arm.

A measurement on the dev corpus (2026-09-16, issue #403 thread) found that overlap 20
already copies about 12 tokens at 57 % of prose boundaries. The informative comparison is
`0` against `20`, and today `0` is impossible to configure.

The clamp is also too loose. `min(20, chunk_size)` lets the overlap equal the chunk size.
At a 16-token child size, the PR #402 review measured 73 near-duplicate chunks that differ
by one word. The operator chose a tighter clamp on 2026-09-26: `min(configured,
child_chunk_size // 2)`.

## What Changes

- Add `data_manager.chunking.chunk_overlap` (int, default `20`), the child-splitter overlap
  for the `sentence` and `markdown` strategies.
- Replace the clamp `min(20, chunk_size)` with `min(configured, chunk_size // 2)` for every
  child splitter. On the `sentence` path the clamped size stays `min(parent_chunk_size,
  child_chunk_size)`, as today. The markdown parent splitter keeps overlap `0`.
- `VectorStoreManager` reads the key in `src/data_manager/vectorstore/manager.py` and passes
  it to `build_hierarchical_nodes`. A value that is not a non-negative integer (a bool, a
  string, a float, a negative number) fails at manager construction with a message that
  names the key, not at each document during ingest.
- `src/cli/templates/base-config.yaml` emits `chunking.chunk_overlap` only when it is set,
  with the `is defined and is not none` guard, so a configured `0` renders as `0`.
- `scripts/benchmarking/measure_chunk_overlap.py` mirrors the production clamp
  (`clamp_overlap`, line 312). Change it to the new rule so its reported "effective overlap"
  stays true.
- `docs/docs/configuration.md` documents the key, its default, the clamp, and that the
  top-level `chunk_overlap` is the `character` path's setting.

**BREAKING (small child sizes only):** with the default overlap of 20, a child size below
40 tokens now gets `child_chunk_size // 2` instead of 20. The default size (512) and every
size of 40 or more chunk exactly as before.

## Out of scope

- Running the sweep, adding the arm file, and any edit under `deploy/**` (operator, with
  #396).
- The top-level `chunk_overlap` of the `character` path.
- Reporting the effective chunking parameters in provenance (#489, armed separately; it will
  read `chunking.chunk_overlap`).

## Impact

- Code: `src/data_manager/vectorstore/node_parsing.py`,
  `src/data_manager/vectorstore/manager.py`, `src/cli/templates/base-config.yaml`,
  `scripts/benchmarking/measure_chunk_overlap.py`.
- Tests: `tests/unit/test_node_parsing.py`,
  `tests/unit/test_vectorstore_manager_hierarchical.py`,
  `tests/unit/test_base_config_chunking_render.py`,
  `tests/unit/test_measure_chunk_overlap.py`.
- Docs: `docs/docs/configuration.md`.
- A changed overlap re-chunks nothing already ingested; a re-ingest applies it.
