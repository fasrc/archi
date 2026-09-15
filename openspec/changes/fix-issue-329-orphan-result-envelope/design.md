# Design — hidden result envelopes never reach the job listing

## Context

`EvaluationJobManager` (`src/evaluation/qa/jobs.py:37`) persists one JSON file per job in a
flat directory and re-reads that directory whenever it needs the set of jobs. Three methods
do it:

| Line | Method | What it wants |
|---|---|---|
| `:60` | `_interrupt_stale_jobs` | every job record, to mark the active ones interrupted |
| `:76` | `_active` | every job record, to enforce single-flight |
| `:387` | `list()` | every job record, for the console catalog |

All three glob `"*.json"`. The subprocess path writes a fourth kind of file into that same
directory — the worker's result envelope at `jobs_dir / f".{job_id}.result.json"`
(`src/evaluation/qa/jobs.py:218`) — and relies on the leading dot to keep it out of those
listings. That is the bug: the leading dot hides a file from the shell and from the `glob`
module, but **not** from `pathlib.Path.glob`, which is what this class uses.

The envelope's shape is checked at `src/evaluation/qa/jobs.py:262-266`: exactly `{"result":
{...}}` on success, or `{"error": …}` from the worker on failure. Neither carries `"id"` or
`"status"`.

That difference decides which of the three sites is actually broken today:

- `_interrupt_stale_jobs` and `_active` both read `job.get("status")` and compare against a
  set of active statuses. An envelope answers `None`, so it falls through harmlessly. These
  two sites are **latent**, not broken.
- `list()` appends whatever it read and returns it. `console.list_jobs`
  (`src/evaluation/qa/console.py:150-154`) then does `job["id"]` on each element, so the
  envelope raises `KeyError` — and the catalog route has no handler for it. This site is
  **live**.

`_execute_process` removes the envelope in a `finally` (`src/evaluation/qa/jobs.py:283`),
under `self._lock`. `list()` takes no lock. So even with no crash at all there is a window
on every job completion where a catalog request 500s.

## Goals / Non-Goals

**Goals:**

- No hidden file can be read as a job record, at any of the three sites.
- An envelope orphaned by a killed process is gone by the time the console is served again.
- The console catalog cannot 500 because of the contents of the jobs directory.

**Non-Goals:**

- Adding a `try` / `except` to the catalog route. See "The route is not the fix" below.
- Locking `list()`. See "The lock is not the fix" below.
- Moving the envelope out of `jobs_dir` into a directory of its own. See the rejected
  alternative below.
- Any change to the worker (`src/evaluation/qa/worker.py`) or to the envelope's shape. The
  envelope is a contract between two processes and this change does not touch it.
- Reporting the defect upstream. That happens on archi-physics/archi PR #608 after this
  merges, and is the operator's action, not this change's.

## Decisions

### One helper owns the glob

`_job_files()` returns `self.jobs_dir.glob("[!.]*.json")`, and the three sites call it.

The alternative — edit the pattern in three places — was rejected because the three sites
drifting apart is precisely how this defect survived review. A reader at
`src/evaluation/qa/jobs.py:60` cannot tell from that line that `:387` needs the same rule,
and nothing fails if only two of the three are fixed. A named helper makes "a job file is a
non-hidden `.json` in `jobs_dir`" one statement that the class reads from one place.

`[!.]*.json` is `fnmatch` syntax and `pathlib` supports it. Verified against the project
interpreter on 2026-08-25: globbing a directory holding `.j1.result.json` and `j2.json`
returns both for `*.json` and only `j2.json` for `[!.]*.json`.

The pattern cannot exclude a real job. `_path` (`src/evaluation/qa/jobs.py:51-57`) round-
trips `job_id` through `uuid.UUID` and rejects anything whose canonical string differs, so
every job file this class writes is named `<uuid>.json` — hexadecimal and hyphens, never a
leading dot.

### The sweep runs at construction, after the interrupt pass

`__init__` calls `_interrupt_stale_jobs()` today (`src/evaluation/qa/jobs.py:48`). It gains
a `_sweep_orphan_results()` call directly after it.

**Why construction is the right moment.** A manager that has just been constructed holds an
empty `self._futures` and an empty `self._processes`. No worker it owns can be writing an
envelope, because it owns none yet. So every envelope on disk at that instant was written by
a process that is no longer running — which is the definition of orphaned. No heuristic, no
timestamp comparison, and no cross-checking against job records is needed to establish it.

**Why after, not before.** `_interrupt_stale_jobs` is what turns a `running` record into an
`interrupted` one. Running the sweep first would delete the envelope of a job the manager
has not yet given up on, and for the two seconds in between the on-disk state would say a
job is running with its result already thrown away. Ordering the two passes the other way
round means the directory only ever moves from a consistent state to a consistent state.

**Why the shape check.** The sweep deletes only names matching `.<uuid>.result.json`, with
the middle segment parsed by `uuid.UUID` exactly as `_path` parses a job id. `jobs_dir` is
host-mounted (`/root/archi/evaluations` in the deployed configuration), so it is a directory
a human can reach. A sweep written as "delete every dot-file" would be a startup routine
that deletes an operator's notes; one written as "delete every `.*.result.json`" would still
delete a file a human named that way while debugging. Matching the exact shape this class
writes keeps the sweep's authority to the files this class created.

A `FileNotFoundError` during the unlink is ignored, reusing `_remove_result`
(`src/evaluation/qa/jobs.py:286-291`) rather than adding a second way to delete the same
kind of file.

### `list()` skips a record with no `"id"`

`list()` already swallows one class of bad file: `read_json` raising `ValueError` is caught
and the file skipped (`src/evaluation/qa/jobs.py:388-391`). A dict with no `"id"` is the
same category of problem — a file in `jobs_dir` that is not a job record — and it gets the
same treatment.

This is deliberately redundant with the two decisions above, and the proposal flags it for
the reviewer as droppable. The argument for keeping it: `list()`'s caller subscripts `["id"]`
without a guard, one directory-listing away from a route with no exception handler. The two
fixes above remove the only *known* writer of an id-less file into that directory. This one
removes the *consequence*, so a future writer — an upstream port, a debugging leftover, a
half-written file — costs a missing row in the console instead of the whole console.

The argument against, recorded honestly: a silent skip can hide a real bug. It is accepted
here because the method's established contract is already "skip what is not a job record",
and because the failure it replaces is a dead page rather than a wrong answer.

## Risks / Trade-offs

**The transient window is closed, but not by locking.** After this change `list()` never
reads the envelope, so the completion-time race has no observable effect. It is still true
that `list()` reads the directory without `self._lock` and can therefore observe a job record
mid-write. That is pre-existing, `write_json` behaviour decides whether it matters, and it is
out of scope here — but a reviewer should not read this change as having made `list()`
thread-safe.

**A sweep is a delete, run at startup, on a host-mounted volume.** The shape check is what
bounds it. The tests pin that bound directly: a file named `.notes.result.json` and a file
named `notes.json` both survive a construction that deletes a real `.<uuid>.result.json`.

**The envelope of a job that finishes during shutdown is dropped.** If the process is killed
after the worker writes the envelope, the result is lost — the next startup deletes it. That
is not a regression: today that job's record is already rewritten to `interrupted` by
`_interrupt_stale_jobs`, and nothing ever reads the orphaned envelope back. This change ends
the pretence that the file is still useful.

## Rejected alternatives

**Give the envelopes their own directory.** Writing to `jobs_dir / "results"` would make the
glob correct by construction and need no pattern change. Rejected: `_execute_process` passes
`str(result_path)` to the worker subprocess as `argv[2]`
(`src/evaluation/qa/jobs.py:230-234`), and the deployed evaluations volume already holds
envelopes at the current path from live trial runs. Moving them needs a migration for a
directory a human owns, to fix a listing bug that a pattern fixes. It also does nothing for
the orphan sweep, which would still have to be written.

**Filter on the record instead of the filename** — keep `"*.json"` and drop anything without
an `"id"`. Rejected as the primary fix: it treats the symptom at one of the three sites and
leaves `_interrupt_stale_jobs` and `_active` reading and parsing files that are not theirs.
It is kept only as the third, defence-in-depth decision above.

**Have the worker write the envelope outside `jobs_dir` entirely**, for example under
`tempfile.gettempdir()`. Rejected: the envelope has to survive the parent process losing the
child, which is the whole reason it is a file and not a pipe, and a container's temp
directory is not host-mounted. The evaluations volume is the durable place.

## Migration Plan

None. The change is backward compatible in both directions: existing job records are named
and read exactly as before, and a deployment that rolls back simply returns to globbing
`"*.json"` over a directory the new code left clean. The first startup after deploy removes
any envelope stranded by earlier crashes, which is the recovery step a human does by hand
today.

## Open Questions

None. The issue body (#329) specifies the fix and the acceptance criteria, and every anchor
it cites was re-verified against `origin/dev` at `44f90abc` on 2026-08-25.
