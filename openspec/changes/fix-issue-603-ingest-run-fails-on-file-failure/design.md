## Context

`update_vectorstore` (`src/data_manager/vectorstore/manager.py:311`) calls
`_sync_vectorstore` and passes its return value to `_record_ingest_run`. If the sync
raises, it records `failed` and raises again. `_sync_vectorstore` (`:328`) returns
`up_to_date` when the hash sets match, else `updated`. It changes `updated` to `failed`
only in the `except` around `self._add_to_postgres(...)` (`:385`–`:394`).

`_add_to_postgres` (`:575`) has two stages:

1. **Process stage.** A `ThreadPoolExecutor` runs the inner `process_file(filehash,
   file_path)` for each file. It returns a payload tuple, or `None` when the file is
   skipped. The loop at `:741` collects the results. A `future.result()` exception is
   caught at `:745`.
2. **Store stage.** For each processed file, it opens a savepoint, embeds (flat path) and
   inserts, and marks the file `embedded`. On an exception it rolls back to the savepoint
   and marks the file `failed`.

Every per-file failure is caught, marked `failed` in the catalog, and skipped. The method
returns `None`. Line numbers are on `origin/dev` `1ca97e24`.

## Goals / Non-Goals

**Goals**
- A sync in which one or more files hit a system error records the run as `failed`.
- A sync in which files only have content outcomes, or no failures, records `updated`.
- Per-file isolation stays: a failed file does not stop the other files.

**Non-Goals**
- Changing any per-file catalog status or error text.
- Changing the run status when `_add_to_postgres` raises (already `failed`).
- Changing the `up_to_date` decision, or the retry of failed files on the next run (a
  failed file has no chunks, so the next run sees its hash as missing and tries it again,
  as it does today).
- Changing `trigger_embedding` in `src/interfaces/chat_app/app.py`, which ignores the
  return value of `update_vectorstore`.
- Changing the log line "All files have been added to the vectorstore".

## Decisions

### D1. Classify each per-file handler against the operator rule

The operator rule (issue #603, "Decision", 2026-10-20): system errors fail the run;
"Unsupported file format" and "No text chunks could be extracted" do not.

| Site (`origin/dev` `1ca97e24`) | Outcome | Class | Counted |
|---|---|---|---|
| `process_file`: `self.loader(file_path)` raises (`:599`–`:606`) | catalog `failed`, `str(exc)` | system error (load) | yes |
| `process_file`: `loader is None` (`:608`–`:612`) | catalog `failed`, "Unsupported file format" | content outcome | no |
| `process_file`: `loader.load()` raises (`:615`–`:622`) | catalog `failed`, `str(exc)` | system error (load) | yes |
| `process_file`: hierarchical, no parents (`:632`–`:639`) | catalog `failed`, "No text chunks could be extracted" | content outcome | no |
| `process_file`: flat, no chunks (`:713`–`:718`) | catalog `failed`, "No text chunks could be extracted" | content outcome | no |
| Batch loop: `future.result()` raises (`:743`–`:752`) — any other exception in `process_file`, for example from `_load_file_metadata`, `_build_hierarchical_payload`, or `text_splitter.split_documents` | catalog `failed`, `str(exc)` | system error (process) | yes |
| Store stage, hierarchical: insert, parent cleanup, or `embedded` update raises (`:826`–`:837`) | savepoint rolled back, row `failed` | system error (store) | yes |
| Store stage, flat: `embed_documents` raises (`:862`–`:884`) | savepoint rolled back, row `failed` | system error (embed) | yes |
| Store stage, flat: chunk insert or `embedded` update raises (`:939`–`:948`) | savepoint rolled back, row `failed` | system error (store) | yes |

An exception that escapes these handlers (for example, a failed `ROLLBACK TO SAVEPOINT`,
or `get_document_id` at `:791` or `:889`) makes `_add_to_postgres` raise. The existing
`except` in `_sync_vectorstore` records that run as `failed`. No change there.

A failed NLTK warm-up (`:731`–`:734`) makes each hierarchical file fail in
`_build_hierarchical_payload`. That reaches the batch loop as a `future.result()`
exception, so it is counted. This agrees with issue #119: each file fails on its own.

### D2. Return the count of system-error files from `_add_to_postgres`

Add a module-level marker directly above `SUPPORTED_DISTANCE_METRICS`:

```python
# process_file's result for a file that hit a system error (#603). A content
# outcome (unsupported format, no text) returns None and does not fail the run.
_FILE_SYSTEM_ERROR = object()
```

In `_add_to_postgres`:

- Change the return annotation to `-> int`. Add one docstring sentence: it returns the
  number of files that hit a system error (#603).
- The early `if not files_to_add: return` becomes `return 0`.
- In `process_file`, the two load handlers (`:606` and `:622`) return
  `_FILE_SYSTEM_ERROR` instead of `None`. The three content-outcome returns stay `None`.
- Before the `ThreadPoolExecutor` block, add `failed_hashes: set = set()`.
- In the batch loop, add `failed_hashes.add(filehash)` in the `except` before
  `continue`. Replace `if result:` with:

  ```python
  if result is _FILE_SYSTEM_ERROR:
      failed_hashes.add(filehash)
  elif result:
      processed_results[filehash] = result
  ```

- In each of the three store-stage `except` blocks, add `failed_hashes.add(filehash)` as
  the first statement after the `logger.error(...)` call.
- After the final `logger.info("All files have been added to the vectorstore")`, add
  `return len(failed_hashes)`.

Why a marker and not a shared counter: `process_file` runs in worker threads. A marker
keeps all counting in the main thread, so no lock is necessary. Why a set: each file is
counted once even if a later change makes two handlers see one file.

### D3. Fail the run in `_sync_vectorstore` when the count is not zero

Keep the `try`/`except` at `:385`–`:394` as it is. Assign the return value and add an
`else` branch:

```python
try:
    failed_files = self._add_to_postgres(
        files_to_add, embedding_progress=embedding_progress
    )
except Exception as e:
    ...  # unchanged
    run_status = "failed"
else:
    if failed_files:
        # A system error on any file leaves the corpus partial (#603).
        # Content outcomes (unsupported format, no text) are not counted.
        logger.error(
            "%d of %d files failed with a system error; "
            "recording the ingest run as failed",
            failed_files,
            len(files_to_add),
        )
        run_status = "failed"
```

### D4. Update the two test stand-ins for `_add_to_postgres`

In `tests/unit/test_vectorstore_reingest_chunk_refresh.py`,
`test_stale_hash_is_removed_and_re_added` and
`test_sync_vectorstore_threads_embedding_progress_to_add_to_postgres` replace
`_add_to_postgres` with a bare `MagicMock()`. A bare `MagicMock()` returns a truthy mock,
which D3 reads as "files failed". Change those two to `MagicMock(return_value=0)`, which
is the new contract for a batch with no system error. Their assertions do not change.
`test_unchanged_corpus_does_not_refresh` never calls `_add_to_postgres`; leave it.

### D5. Docs

`docs/docs/services.md`, "Unknown and failure states", bullet "**⚠ the most recent
ingest attempt failed at …**": add these two sentences at the end of the bullet:

> A run is failed when the sync stops with an error, or when one or more documents hit a
> system error (an error when the file is loaded, processed, or embedded, or a store
> failure). A document with an unsupported format or with no text is marked failed in the
> catalog, but it does not fail the run.

## Risks / Trade-offs

- **More `failed` runs.** A deployment with one document that always fails to embed now
  records every ingest run as `failed`, and the board keeps showing an older successful
  run. That is the purpose of the operator rule: the corpus is partial. The failed
  document is visible in the catalog with its error text.
- **Tracked tasks report an error.** `run_upload_update` and `run_source_refresh`
  (`src/utils/ingestion_status.py`) raise on a returned `failed`, so such a run shows as an
  error in the ingestion status. This is the existing contract for `failed`.

## Measurement

On `origin/dev` `1ca97e24`, a throwaway test with `_setup_flat_manager`, two files, and
an `execute_values` that raises for the first file, gave: `update_vectorstore()` returned
`updated`, `_record_ingest_run` received `updated`, `hash-0` was marked `failed`
(`store boom`), and `hash-1` was marked `embedded`. The run-status test in task 2.1 is
the committed form of this measurement.
