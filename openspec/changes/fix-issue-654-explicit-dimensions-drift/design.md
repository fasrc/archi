## Context

`src/utils/ingest_provenance.py` builds the ingest-config snapshot (`build_ingest_config_snapshot`) that `src/data_manager/vectorstore/manager.py:419` stores on each `ingest_run` row, and compares it with the live config (`compare_ingest_config`) for the status board (`src/interfaces/chat_app/status_provenance.py:185`).

PR #646 (issue #565) changed the dimension default from a flat 384 to a per-model table (1536 for `OpenAIEmbeddings`). To keep old rows from showing false drift, it added a skip in `compare_ingest_config`: recorded 384 + same model + current value equals the model default → no drift. That skip cannot tell an old row from a new row that recorded an explicit 384.

## Goals / Non-Goals

**Goals:**
- A snapshot written after this change reports drift for the issue's repro.
- Rows written before this change keep the legacy compatibility.

**Non-Goals:**
- No backfill of old rows. No schema or template change. No change to the dimension resolution itself (explicit, then the per-model table, then 384).

## Decisions

### D1 — A boolean marker key, `embedding_dimensions_explicit`

`build_ingest_config_snapshot` adds `"embedding_dimensions_explicit": "dimensions" in embedding_entry`, where `embedding_entry` is the mapping already used for `embedding_dimensions`. "Explicit" means the key is present, which is the same test that `embedding_entry.get("dimensions", ...)` uses in the snapshot and in `manager.py:162-165`. A present `dimensions: null` counts as explicit, because both of those call sites then use `None`. The value is always a Python `bool`.

The key goes into `INGEST_CONFIG_KEYS` directly after `"embedding_dimensions"`, so `test_snapshot_covers_exactly_the_declared_key_set` stays true.

Alternative rejected: a snapshot version number. One boolean answers the only open question, and the existing "key absent means the snapshot predates the key" rule in `compare_ingest_config` already handles old rows.

### D2 — Legacy skip only for a snapshot without the marker

The skip condition in `compare_ingest_config` gains one more term: `"embedding_dimensions_explicit" not in recorded`. With the marker present, `embedding_dimensions` is compared by value only, as every other key is.

Truth table (model unchanged, `OpenAIEmbeddings`, default 1536):

| recorded dims | recorded marker | current dims | result |
|---|---|---|---|
| 384 | absent | 1536 | no drift (legacy row) |
| 384 | absent | 768 | drift (current is not the default) |
| 384 | `True` | 1536 | **drift** (the issue's repro) |
| 1536 | `True` | 1536 (marker `False`) | no drift |
| 1536 | `False` | 1536 | no drift |

### D3 — The marker is never a drift entry

`compare_ingest_config` skips the key `embedding_dimensions_explicit` in its loop (`continue` before the equality check). Reason: explicit 1536 → default 1536 does not change the vectors, so reporting it is noise; when the explicit flag change does change the dimension, the `embedding_dimensions` entry already reports it. The marker stays in the snapshot and so in the status page config table (`status.html:350` renders every key), which is honest provenance.

### D4 — Existing tests that change

Three existing tests in `tests/unit/test_ingest_provenance.py` encode the old snapshot shape and must change in the same commit:

- `test_snapshot_reads_every_flag_from_a_full_config` (exact dict, about line 41; its config sets `dimensions: 1536`) gains `"embedding_dimensions_explicit": True`.
- `test_snapshot_applies_the_ingest_paths_own_defaults_when_keys_are_absent` (exact dict, about line 74) gains `"embedding_dimensions_explicit": False`.
- `test_compare_no_embedding_dimensions_drift_for_pre_fix_row_with_old_default` builds its "old row" with the builder, which now writes the marker. The old row must not carry it: remove the key from `at_ingest` (`at_ingest.pop("embedding_dimensions_explicit")`) after the `dict(...)` copy. The test intent (a pre-fix row) is unchanged.

`test_compare_embedding_dimensions_drift_when_current_is_not_model_default` and `test_compare_embedding_dimensions_drift_when_model_changed` pass without a change.

### D5 — Docs

In `docs/docs/services.md` (the "Ingest configuration (as used at ingest)" bullet, line 91), make two edits:

1. Replace "Rows recorded before this rule stored 384 for every model; for those rows a change from 384 to the model's default is not reported as drift." with "Rows recorded before this rule stored 384 for every model. For a row without `embedding_dimensions_explicit`, a change from 384 to the model's default is not reported as drift."
2. Replace "An explicit `dimensions: 384` cannot yet be told apart from that old default (follow-up [#654](https://github.com/fasrc/archi/issues/654))." with "Runs recorded after [#654](https://github.com/fasrc/archi/issues/654) also save `embedding_dimensions_explicit`, which says whether `dimensions` was set; for those runs every change of the dimension is reported as drift. On a run without that field, an explicit `dimensions: 384` that was later removed still does not show as drift until the next ingest."

Reason: "this rule" in that bullet means the per-model default table from PR #646, which is older than this change. Runs recorded between #646 and this change have no marker, so the docs must tie the exception to the marker, not to #646.

## Risks / Trade-offs

- A row recorded before this change with an explicit 384 still hides the drift. Nothing in the old row can fix that; a re-ingest writes a marker. The D5 docs text ties the claim to runs that carry the marker and states the remaining gap.
- The status page shows one more config row. No template change is necessary.
