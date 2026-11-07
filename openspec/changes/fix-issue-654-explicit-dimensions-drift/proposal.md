## Why

The status board cannot tell an explicit `dimensions: 384` from the old flat default of 384 in an ingest snapshot, so it hides a real embedding-dimension drift (issue #654, found in review of PR #646).

On `origin/dev` at `0ed50a23`, `compare_ingest_config` in `src/utils/ingest_provenance.py:218-230` skips `embedding_dimensions` drift when the recorded value is 384, the model is unchanged, and the current value is the model's default. The snapshot has no field that says whether 384 was explicit, so the skip also fires for a snapshot that recorded an explicit 384.

Repro:

1. Ingest with `embedding_name: OpenAIEmbeddings` and `embedding_class_map: {OpenAIEmbeddings: {dimensions: 384}}`. The snapshot stores 384.
2. Remove the `dimensions` override. The live snapshot now resolves 1536.
3. `compare_ingest_config(now, then)` returns `[]`. The board says the configuration is unchanged, but the corpus vectors have 384 dimensions.

## What Changes

- `build_ingest_config_snapshot` records a new boolean key, `embedding_dimensions_explicit`: `True` when the named embedder's `embedding_class_map` entry has a `dimensions` key, else `False`. The key joins `INGEST_CONFIG_KEYS`.
- `compare_ingest_config` applies the legacy 384 compatibility skip only when the recorded snapshot has no `embedding_dimensions_explicit` key (a row written before this change). A snapshot that carries the key gets a plain value comparison of `embedding_dimensions`.
- `compare_ingest_config` never reports `embedding_dimensions_explicit` itself as a drift entry. The key is provenance for the comparison, not an ingest setting: a change from an explicit 1536 to the default 1536 does not change the corpus.
- `docs/docs/services.md` replaces the "cannot yet be told apart" sentence with the new rule.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest-run-provenance`: the snapshot records whether the dimensions were explicit; legacy compatibility is limited to snapshots without that record.

The `ingest-run-provenance` capability lives only in the unarchived changes `add-ssb-deployment-provenance` and `fix-issue-565-status-provenance-honesty`, so this change's delta uses `ADDED` requirements, not `MODIFIED`.

## Impact

- Code: `src/utils/ingest_provenance.py` only.
- Tests: `tests/unit/test_ingest_provenance.py`.
- Docs: one paragraph in `docs/docs/services.md`.
- No schema change (the snapshot is a JSONB column). No change to `app.py`, the template, or the deploy. Existing `ingest_run` rows keep their recorded values and keep the legacy compatibility. The status page config table shows one more row, `embedding_dimensions_explicit`, for runs recorded after this change.
