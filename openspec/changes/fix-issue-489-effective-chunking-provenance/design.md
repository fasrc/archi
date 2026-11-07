## Context

`src/utils/ingest_provenance.py` is the pure, tested single definition of the
ingest-affecting flags. It restates the ingest defaults instead of importing the data
manager, because the chat app imports it and must not pull in the ingest dependency tree
(module docstring, `:14-17`). Its tests name the origin of each default to catch drift
(`tests/unit/test_ingest_provenance.py:83` for `child_chunk_overlap`).

## Decisions

### D1. One public helper, three consumers

Add `effective_chunking(data_manager_config) -> dict` to `ingest_provenance.py`.
`build_ingest_config_snapshot` puts its result under `effective_chunking`. The benchmark
stamp and `/config/static` call the same helper, so the three surfaces cannot disagree.

### D2. Path rule mirrors the manager

- `strategy = chunking.strategy`, default `"sentence"` (as `_resolve_chunking_strategy`).
- Hierarchical when `strategy in ("sentence", "markdown")`; otherwise character (as
  `manager.py:169-172`). An unknown strategy is reported as `"character"`, because the
  manager then uses the `CharacterTextSplitter`.

Hierarchical result:
`{"path": "hierarchical", "strategy": <s>, "parent_chunk_size": P, "child_chunk_size": C,
"child_chunk_overlap": O}` where `P`/`C` default to 2048/512 independently (as
`_resolve_chunk_sizes`), and `O` is the clamped overlap the node parser applies:

- `sentence`: `max(0, min(overlap, min(P, C) // 2))` (`node_parsing.py:264-266`);
- `markdown`: `max(0, min(overlap, C // 2))` (`node_parsing.py:523-526`);
- `overlap` = `chunking.chunk_overlap`, default 20; `None` reads as 20 (as
  `_resolve_chunk_overlap`).

Character result: `{"path": "character", "strategy": <s>, "chunk_size": <top-level,
default 1000>, "chunk_overlap": <top-level, default 150>}`.

The issue's example omits `strategy`; it is added because `sentence` and `markdown` clamp
differently, so the values alone are ambiguous. Acceptance tests compare against the dict
with `strategy` included.

### D3. Never raise

Provenance must never fail an ingest (`_mapping` docstring). If `P`, `C`, or `overlap` is
not a non-bool int (or overlap is negative), report the configured value as is, with no
clamping. The manager refuses such a config at construction, so no ingest runs with it.

### D4. Defaults are restated, with drift-guard tests

Restate 2048 / 512 and the strategy names as module constants. Add tests that import
`DEFAULT_PARENT_CHUNK_SIZE`, `DEFAULT_CHILD_CHUNK_SIZE`, `CHILD_CHUNK_OVERLAP`,
`SENTENCE_STRATEGY`, `MARKDOWN_STRATEGY`, and `_clamped_overlap` from `node_parsing.py`
and assert equality with the restated values (follow the pattern of the existing
`test_snapshot_child_overlap_default_mirrors_the_node_parser`).

### D5. Benchmark stamp: live only, present only with a data_manager section

In the live builder (the function that returns `key_settings` at
`benchmark_provenance.py:779`), add `key_settings["effective_chunking"] =
effective_chunking(basis["data_manager"])` only when `basis.get("data_manager")` is a
mapping. With no `data_manager` section, omit the key: defaults would claim a chunking
nobody configured (same rule as `settings_at_paths`, which omits absent paths). The key is
not a dotted config path, which marks it as derived. The reconstructed stamp
(`reconstruct_version_stamp`, `:836`) does not get the key: it rebuilds old artifacts, and
today's defaults can differ from the defaults in force when that run happened.

### D6. `/config/static`

`StaticConfig.data_manager_config` already holds the seeded `data_manager` section
(`src/utils/config_service.py:56`, `src/cli/tools/config_seed.py:98`). In `api.py` add
`"effective_chunking": effective_chunking(config.data_manager_config),` to the response.
`api.py` is not imported by unit tests, so all logic stays in the helper and the one new
line is the only uncovered line.

### D7. Status board and drift

No change to `status_provenance.py`. The new key flows through
`build_ingest_config_snapshot` at both the write path (`manager.py:405`) and the read path
(`status_provenance.py:225`). `compare_ingest_config` compares dicts with `!=`, and skips a
declared key absent from an older snapshot (`ingest_provenance.py`, the
`keys = [key for key in INGEST_CONFIG_KEYS if key in recorded]` line), so a run recorded
before this change shows no false drift.

## Risks

- The template renders the dict value with its default string form. That is readable
  enough for a provenance panel; a nicer rendering is out of scope.
