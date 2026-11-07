## Why

`VectorStoreManager._sync_vectorstore` (`src/data_manager/vectorstore/manager.py:328`)
sets `run_status = "updated"` (`:361`) and changes it to `"failed"` (`:394`) only when
`_add_to_postgres` raises. `_add_to_postgres` (`:575`) catches every per-file failure,
marks that file `failed` in the catalog, and continues. It returns `None` and gives no
failure signal. As a result, a sync in which N of M documents fail records the ingest run
as `updated`. The status board (`src/interfaces/chat_app/status_provenance.py:68`) then
shows that run as the newest successful run, so it presents a partial corpus as a
complete one (issue #603; first found by the Codex review on PR #601, thread
`4163189467`).

Measured on `origin/dev` `1ca97e24` with a throwaway test that uses the flat-path helper
`_setup_flat_manager` from `tests/unit/test_vectorstore_manager_batch_commit.py`: two
files, the chunk insert of the first file raises.

| Value | Result now |
|---|---|
| `update_vectorstore()` return value | `updated` |
| Status passed to `_record_ingest_run` | `updated` |
| Catalog mark of the first file | `failed` (`store boom`) |
| Catalog mark of the second file | `embedded` |

The operator decided the rule on 2026-10-20 (issue body, "Decision"): **only system
errors fail the run.** A system error is an exception while the file is loaded or
processed, an exception while it is embedded, or a store failure (a rolled-back
savepoint or a database error). The content outcomes "Unsupported file format" and "No
text chunks could be extracted" stay per-file `failed` catalog rows and do not change the
run status.

## What Changes

- `_add_to_postgres` returns the number of files that hit a system error (`0` when none).
  Each per-file handler is classified against the operator rule (design D1). The two
  process-stage `except` handlers in `process_file` return a module-level marker
  `_FILE_SYSTEM_ERROR` instead of `None`. The batch loop counts that marker, a
  `future.result()` exception, and the three store-stage `except` handlers. The content
  outcomes still return `None` and are not counted.
- `_sync_vectorstore` sets `run_status = "failed"` when that number is not zero, and logs
  how many files failed. The existing path for an `_add_to_postgres` that raises does not
  change.
- Per-file isolation does not change. A file that fails still does not stop the other
  files, and those files are still marked `embedded`.
- `docs/docs/services.md` says when a run is `failed`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingest-run-provenance`: adds one requirement. A system error on any document fails
  the ingest run, and a content outcome does not. (The capability is not archived under
  `openspec/specs/`, so the delta uses `## ADDED Requirements`.)

## Impact

- Code: `src/data_manager/vectorstore/manager.py` (`_add_to_postgres`, its inner
  `process_file`, and `_sync_vectorstore`; one new module-level marker).
- Tests: `tests/unit/test_vectorstore_manager_batch_commit.py` (new tests);
  `tests/unit/test_vectorstore_reingest_chunk_refresh.py` (two `MagicMock` stand-ins for
  `_add_to_postgres` get `return_value=0`, the new return contract).
- Docs: `docs/docs/services.md` (one bullet).
- Behavior: a run that was `updated` with some system-error files is now `failed`. The
  status board then keeps the previous successful run as the corpus run and shows
  "⚠ the most recent ingest attempt failed at …". The tracked upload and source-refresh
  tasks in `src/utils/ingestion_status.py` (`_raise_if_failed`, `:102`) already treat a
  returned `failed` as an error, so those tasks now report an error for such a run. That
  is the intended effect of the operator rule.
- Data: no schema change. `failed` is already a valid `ingest_run` status
  (`src/utils/ingest_run.py:26`).
