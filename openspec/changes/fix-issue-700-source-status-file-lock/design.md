## Context

The uploader runs inside the data-manager process: `service_data_manager.main()` builds
`FlaskAppWrapper(app, post_update_hook=trigger_update, status_file=status_file)`
(`src/bin/service_data_manager.py:235-237`). So every writer of `ingestion_status.json` runs
in one process, and one process-wide `threading.Lock` covers all of them (operator decision
on issue #700, 2026-10-20).

Writers and readers on `origin/dev` `e83160e6`:

| Code | Kind | Thread |
|---|---|---|
| `set_source_status` (`service_data_manager.py:98-108`) via `run_source_refresh` | write `running` / `idle` / `error` | scheduler thread, inside `ingestion_lock` |
| start-up seed (`:202-216`) | write `schedule` + `state` (setdefault) | main thread, before the scheduler and Flask start |
| `reload_schedules` (`:249-261`) | write `schedule` | Flask request thread |
| `FlaskAppWrapper._update_source_status` (`uploader_app/app.py:527-556`) | write `state` / `last_run` / `schedule` | Flask request thread |
| `load_status` in `create_job_callback` (`:185-193`) and `get_schedule_status` (`:277-286`) | read | scheduler / request thread |

## Goals / Non-Goals

**Goals:** two writes that overlap keep both updates; no reader can see a partly written
file; one module owns every read and write of the file.

**Non-Goals:**
- Cross-process locking (`fcntl`). There is one writing process while
  `services.data_manager.flask_debug_mode` is false (the default; no tracked deploy config
  sets it). With it true, Flask turns on Werkzeug's reloader: the first process runs all of
  `main()` (seed, scheduler thread, ingestion thread) and then waits while a child process
  runs `main()` again, so two schedulers write the file and this lock covers only one
  process. That double run exists today and is not changed here. If the uploader ever moves
  to its own process, or debug mode must be safe, this module is the one place to add a file
  lock.
- When the uploader writes its terminal status (#699, PR #802) and status at the start of an
  upload's collection (#648).
- The gap after `run_tracked` releases `ingestion_lock` (PR #802 "Known limit"). This change
  makes each write whole; it does not order a manual ingest's write against a refresh.
- A strict read on the write path. Today an unreadable file reads as `{}` and the next write
  replaces it; that does not change (D6). The PR asks the reviewer to decide.
- Docs: `GET /api/schedules` returns the same fields with the same meaning.

## Decisions

### D1 — One module, `src/utils/source_status_file.py`
Public functions (exact code in tasks.md Appendix A):

- `load_source_status(path) -> dict` — `{}` when the file is absent (no log), unreadable or
  not a JSON object (WARNING).
- `update_source_status(path, source, *, state=None, last_run=None, schedule=None)` — change
  only the given fields of one entry; an empty `schedule` removes the entry's `schedule` (the
  uploader's current rule). Raises on a write error.
- `seed_source_schedules(path, schedules)` — the start-up seed: for each non-empty schedule,
  `setdefault("schedule")` and `setdefault("state", "idle")`. Always writes (today's seed
  always called `save_status`).
- `set_source_schedules(path, schedules)` — `reload_schedules`: set `schedule` only on
  sources that already have an entry.

All four writes go through one private `_mutate(path, change)`: `with _LOCK:` read → call
`change(data)` → `_write_atomic(path, data)`. An entry that is not a mapping is replaced by
`{}` (update, seed) or skipped (set), so content cannot make a write raise.

### D2 — One process-wide `threading.Lock`, a leaf lock
A module-level `_LOCK = threading.Lock()`. It is a leaf: `_mutate` holds it only for the read,
the in-memory change and the write. Code under it takes no other lock of this service; a log
call takes only the logging handler's own lock, and no handler writes this file. The scheduled
refresh calls `set_source_status` while it holds `ingestion_lock`, so the order is always
`ingestion_lock` → `_LOCK`, never the reverse; no deadlock is possible. It is not an `RLock`:
nothing re-enters it.

### D3 — Readers take no lock
With D4, the path always names a whole file (old or new), so a reader needs no lock.
`load_status` keeps its name and becomes `return load_source_status(status_file)`.

### D4 — Atomic write: same-directory temp file, `fsync`, `os.replace`
`_write_atomic` creates the parent directory, writes JSON to
`path.with_name(f".{path.name}.{os.getpid()}.tmp")` with `open(..., "w")`, flushes, `fsync`s,
then `os.replace(tmp, path)`. On any exception it deletes the temp file and re-raises.
- Same directory → same filesystem, so `os.replace` is one atomic rename.
- `open()` honours the umask, so a new file gets the same mode that `write_text` gave a new
  file (`mkstemp` would make it `0600`). `os.replace` swaps the directory entry, so a custom
  mode or owner on the existing file, or a symlink at the path, is not kept (`write_text`
  wrote through them). Nothing in the repository changes the mode of this file or links it;
  it lives in the data-manager volume and only this code creates it.
- The name is unique in the process because `_LOCK` serializes writers. A temp file that a
  crash leaves behind is ignored by readers and overwritten by a later write with that pid.
- `fsync` before the rename keeps a power loss from leaving an empty file under the real name.

### D5 — Error behaviour does not change
- `FlaskAppWrapper._update_source_status` keeps its `try` / `except Exception` and logs
  `Failed to update source status: ...`; it never raises. PR #802 (D4 there) relies on this.
- `set_source_status` in `main()` still raises on a write error; `run_source_refresh` already
  handles a raise from the `error` write (#650).

### D6 — An unreadable file reads as `{}` and is replaced by the next write
Same as today, for two kinds of read failure:
- Content that does not parse, or is not a JSON object. After this change no partial write can
  cause that, so only an outside actor can, and a status file that recovers on the next write
  is better than one that blocks every write.
- A transient `OSError` on an intact file (EIO, EACCES, EMFILE). The next locked write then
  saves `{source: entry}` and erases every other source's entry, including an `error` state.
  The old code did the same. A strict read on the write path (raise, write nothing) would stop
  it, but then the error reaches the callers: the uploader logs it, a scheduled refresh fails
  at its `running` write, and the start-up seed stops `main()`. That trade is for the reviewer
  (PR body), so this change keeps today's rule.

`load_source_status` logs a WARNING in both cases, now for the data manager too (its old
`load_status` was silent). An absent file reads as `{}` with no log.

### D7 — Call sites
- `service_data_manager.py`: delete `import json` and `save_status`; `load_status` body →
  `return load_source_status(status_file)`; `set_source_status` body →
  `update_source_status(status_file, source, state=state, last_run=last_run)`; the seed →
  `seed_source_schedules(status_file, db_schedules)` and the `for` loop keeps only
  `scheduler.add_job(...)`; `reload_schedules` → `set_source_schedules(status_file,
  new_schedules)`. The scheduler starts after the seed in both the old and new order, so moving
  the seed write before the `add_job` loop changes nothing observable.
- `uploader_app/app.py`: `_load_source_status` → `return load_source_status(self.status_file)`
  (kept, so its `def` line stays and PR #802's insertion directly above it merges cleanly);
  `_update_source_status` → `update_source_status(self.status_file, source, state=...,
  last_run=..., schedule=...)` inside the existing `try`. No call site changes, so
  `tests/unit/test_api_reference_schedule_state_contract.py` (regex
  `_update_source_status\(\s*"(\w+)",\s*state="idle"`) still matches.

### D8 — Tests (exact code in tasks.md Appendices B, C and E)
- Race tests pause the FIRST `json.loads` in the thread named `writer-a` (the read inside the
  lock) with `resume.wait(1.0)`, and run the second writer meanwhile. Without the lock the
  second write finishes and the first one saves its old copy over it (red on `origin/dev`).
  With the lock the second writer blocks, so the wait always times out after 1.0 s and the
  result is deterministic. The wait has a timeout, so it can never hang the suite even though
  it runs inside the lock. Patching `json.loads` works for old and new code, because both call
  `json.loads` through the `json` module at call time.
- Atomic write: a spy on `os.replace` reads the target and the temp file at rename time. The
  target still holds the whole old content, the temp file holds the whole new content, and it
  is in the same directory. A second test makes `os.fsync` raise: the old file is unchanged
  and no temp file remains.
- A fence test pins the issue's acceptance grep: neither writer file contains `write_text`,
  `json.loads(self.status_file` or `status_file.read_text`, and both import the module.
- A `main()` test (Appendix E) runs `service_data_manager.main()` to its end with fakes for
  the database, `DataManager`, `CronScheduler`, `instrument_flask_app` and `FlaskAppWrapper`
  (the same pattern as `tests/unit/test_service_data_manager_parent_id_index.py`). It checks
  the seed, a scheduled refresh that fails then succeeds (the real `run_source_refresh` calls
  `set_source_status`), `POST /api/reload-schedules`, and `GET /api/schedules`, all against
  the file. It passes on `origin/dev` too: it pins the wiring, not the race. Loop 1 checked
  that it catches a wrong wiring: removing the seed call, dropping `last_run` from
  `set_source_status`, or removing the reload call each turns it red.

## Risks / Trade-offs

- The three race tests each wait a fixed 1.0 s (about 3 s of suite time). A shorter wait
  would make the red step on old code less certain under load; the green result does not
  depend on the length.
- `fsync` adds a disk flush to each status write on the uploader request path. The file is
  small and written a few times per ingest.
- The `main()` test replaces six collaborators of `main()` with fakes, so a later change to
  `main()` that adds a collaborator can need a new fake there. Patch coverage measured by
  Loop 1 with it: 100 % (82 lines).
