# Keep the worker's hidden result envelopes out of the job listing

## Why

`EvaluationJobManager` writes the worker's result envelope to a hidden file beside the job
records it owns — `jobs_dir / f".{job_id}.result.json"`
(`src/evaluation/qa/jobs.py:218`). Every one of the three listing sites in that class then
globs the directory with `"*.json"` (`src/evaluation/qa/jobs.py:60`, `:76`, `:387`), and
`pathlib.Path.glob` **does** match dot-files. Measured against the project interpreter
(CPython 3.11.15) on 2026-08-25:

```
pathlib  : ['.j1.result.json', 'j2.json']
pathlib! : ['j2.json']            # the same directory, globbed "[!.]*.json"
globmod  : ['globprobe/j2.json']  # the glob module, which hides dot-files
```

So `list()` (`src/evaluation/qa/jobs.py:385`) returns the envelope as if it were a job.
The envelope is `{"result": …}` or `{"error": …}` and carries no `"id"` key, so
`EvaluationConsoleService.list_jobs` (`src/evaluation/qa/console.py:150-154`) subscripts
`job["id"]` and raises `KeyError`. The catalog route
(`src/interfaces/chat_app/evaluation_routes.py:154-167`) is the one route in that blueprint
with **no** `try` / `except` around its service calls — it never reaches the `_error` helper
at `src/interfaces/chat_app/evaluation_routes.py:138` that the other 19 routes use — so the
`KeyError` reaches Flask and every load of the console page answers 500.

Two distinct failures come out of that one glob:

- **A transient 500 on every normal completion.** `_execute_process` removes the envelope
  in its `finally` (`src/evaluation/qa/jobs.py:283`), but `list()` takes no lock, so any
  catalog request that lands while the envelope exists 500s.
- **A permanent 500 after a crash.** If the process dies between the worker writing the
  envelope and that cleanup — SIGKILL during a redeploy, OOM — the file stays on the
  host-mounted evaluations volume forever. `_interrupt_stale_jobs`
  (`src/evaluation/qa/jobs.py:59-73`) does not remove it: it only rewrites files that carry
  an active `status`, and the envelope carries no `status` at all. The console is then dead
  until a human finds and deletes a hidden file.

Found in post-merge review of PR #305 (merge `4314ac4b`). `src/evaluation/qa/jobs.py` is
`port-verbatim` upstream code (pin `bebfbe56`,
`openspec/changes/archive/port-live-eval-trial/disposition.md`), so the same defect is
upstream and gets reported on archi-physics/archi PR #608 after this merges.

## What Changes

- One private helper, `_job_files()`, becomes the single source of the listing glob and
  returns `self.jobs_dir.glob("[!.]*.json")`. The three current call sites
  (`src/evaluation/qa/jobs.py:60`, `:76`, `:387`) call it instead of globbing themselves,
  so the three cannot drift apart again. No job record can be excluded by the new pattern:
  `_path` (`src/evaluation/qa/jobs.py:51-57`) rejects any `job_id` that is not a canonical
  UUID string, and a UUID never starts with a dot.
- `EvaluationJobManager.__init__` sweeps orphaned envelopes, **after**
  `_interrupt_stale_jobs` has run. A freshly constructed manager owns no worker process, so
  any envelope already on disk belongs to a process that is gone. The sweep removes only
  files matching `.<uuid>.result.json` — the exact shape `_execute_process` writes — so a
  dot-file the manager did not write is never deleted from a host-mounted directory.
- `list()` skips a record with no `"id"` key, alongside the malformed-JSON skip it already
  does. This is defence in depth and is called out for the reviewer: the two changes above
  close the known writer of id-less files, and this one makes a `KeyError` out of
  `list_jobs` structurally impossible rather than merely unlikely. It is one task and two
  lines; it can be dropped without affecting the rest.
- New tests in `tests/unit/evaluation/qa/test_jobs_history.py`. No network, no registry,
  `tmp_path` fixtures only.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `qa-evaluation-trial`: three requirements are **ADDED**. The capability exists under
  `openspec/specs/qa-evaluation-trial/spec.md`, but its one console requirement,
  "Evaluations console behind a config toggle", is already being **MODIFIED** by the
  in-flight change `fix-issue-328-eval-console-storage-fail-closed` (PR #352). Touching
  that same requirement here would collide at archive time, and the durability of the job
  store is a separate statement from the console's config toggle in any case.

## Impact

- `src/evaluation/qa/jobs.py` — the shared glob helper, the startup sweep, the id guard.
- `tests/unit/evaluation/qa/test_jobs_history.py` — new tests only; existing tests unmodified.
- `src/evaluation/qa/console.py` and `src/interfaces/chat_app/evaluation_routes.py` are
  **not** edited. The catalog route's missing `try` / `except` is real, but the route is a
  thin call site in a file that unit tests do not import, and this change removes the input
  that makes it 500. Hardening the route itself belongs with #328's fail-closed work.
- Coverage: `src/evaluation/qa/jobs.py` is under `--cov=src`, so every added line reports to
  `diff-cover` and must clear the 80% patch bar.
