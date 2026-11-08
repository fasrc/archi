## Why

`ingestion_status.json` is the per-source status file that `GET /api/schedules` reads. On
`origin/dev` `e83160e6` it has three writers, and each one does an unlocked
read-modify-write of the whole file:

- `set_source_status` in `src/bin/service_data_manager.py:98-108` (`load_status()` → change
  one entry → `save_status()`). The scheduled refresh calls it through `run_source_refresh`
  (`src/utils/ingestion_status.py:115-140`) to write `running`, `idle`, and `error`.
- `reload_schedules` in `src/bin/service_data_manager.py:249-261`: `load_status()`, set
  `schedule` for each source, `save_status()`. The start-up seed at `:202-216` does the same.
- `FlaskAppWrapper._update_source_status` in `src/interfaces/uploader_app/app.py:527-556`: its
  own `_load_source_status()` (`:516-525`) → change → `write_text`.

Two writes that overlap can lose one update. Example (Codex adversarial review on PR #694):
`reload_schedules` reads the file, a scheduled refresh writes `error`, then `reload_schedules`
saves its old copy. The `error` is lost, and the source shows `running` until the next run.

`save_status` (`:94-96`) and the uploader both use `write_text`, which is not atomic. A reader
can see a partly written file; `_load_source_status` then logs a warning and returns `{}`, and
the next write saves that `{}` plus one entry over every other source.

Loop 1 reproduced the lost update on `origin/dev`: two overlapping uploader writes (`schedule`
and `state="error"`) end with `state == "running"` (`1 failed, 2 passed` in the new
`tests/unit/test_uploader_source_status_lock.py`).

Source: issue #700, decided by the operator on 2026-10-20 (`ultracode` tier).

## What Changes

- New module `src/utils/source_status_file.py` owns the file. Every write holds one
  process-wide `threading.Lock` around read → change → write, and writes atomically: a
  temporary file in the same directory, `fsync`, then `os.replace`.
- The three writers (and the start-up seed) call the module. `service_data_manager.py` and
  the uploader no longer read or write the file directly.
- Error handling does not change: the uploader still logs a failed write and does not raise
  (PR #802 relies on this), and the data manager's `set_source_status` still raises
  (`run_source_refresh` handles it, #650).
- No API shape change and no docs change: `GET /api/schedules` returns the same fields; an
  overlapping write no longer loses an update.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `ingestion-source-status`: adds two requirements (overlapping writes keep both updates; a
  reader never sees a partly written file). The capability exists only in the unarchived
  `fix-issue-650-source-status-error` change, so the delta uses `## ADDED Requirements`.

## Impact

- `src/utils/source_status_file.py` (new).
- `src/bin/service_data_manager.py` (`load_status`, `set_source_status`, the start-up seed,
  `reload_schedules`; `save_status` and `import json` removed).
- `src/interfaces/uploader_app/app.py` (`_load_source_status`, `_update_source_status`
  bodies only; every call site is unchanged).
- `tests/unit/test_source_status_file.py` (new), `tests/unit/test_uploader_source_status_lock.py`
  (new), `tests/unit/test_service_data_manager_source_status.py` (new: drives `main()` to pin
  the three data-manager writers to the module).
- Merge order: the issue says to land this before #699. #699 is open as PR #802, which keeps
  every `_update_source_status` call site. Loop 1 merged this change with PR #802's head
  `7b6517aa` with no conflict, and the tests of both passed on the merged tree (`68 passed`:
  the three new files, `test_uploader_manual_ingest_status.py`, the contract test and
  `test_ingestion_status_lock.py`).
