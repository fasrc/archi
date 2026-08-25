## ADDED Requirements

### Requirement: The job store lists only its own job records

`EvaluationJobManager` SHALL treat a file in `jobs_dir` as a job record only when its name does not begin with a dot, and SHALL apply that rule at every site that lists the directory.

The manager writes a second kind of file into `jobs_dir`: the worker's result envelope, at
`jobs_dir / f".{job_id}.result.json"` (`src/evaluation/qa/jobs.py:218`). The leading dot is
the only thing separating the two kinds, and it is doing a job it cannot do —
`pathlib.Path.glob("*.json")` matches dot-files, unlike the shell and unlike the `glob`
module. All three listing sites (`src/evaluation/qa/jobs.py:60`, `:76`, `:387`) use
`pathlib`, so all three read the envelope as a job.

The requirement covers every site, not only the one that visibly fails. `_interrupt_stale_jobs`
and `_active` read `job.get("status")`, which an envelope answers with `None`, so today they
merely waste a read. `list()` returns what it read, and its caller subscripts `job["id"]`, so
today it takes the console down. Fixing only the site that fails would leave the same wrong
rule written in two more places, which is how this defect reached a merged PR.

No job record can be excluded by the rule. `_path` (`src/evaluation/qa/jobs.py:51-57`)
rejects any `job_id` whose canonical `uuid.UUID` string differs from itself, so every job
file the manager writes is named `<uuid>.json` and no UUID begins with a dot.

#### Scenario: A hidden result envelope is not listed as a job

- **WHEN** a file named `.<uuid>.result.json` holding `{"result": {...}}` sits in `jobs_dir` beside one valid job record
- **THEN** `list()` returns exactly the one job record
- **AND** `EvaluationConsoleService.list_jobs()` returns without raising

#### Scenario: A completing job cannot 500 the catalog

- **WHEN** the envelope for a job exists on disk because the job has finished and its cleanup has not yet run
- **THEN** a listing taken at that moment omits the envelope and raises nothing

The envelope is removed inside `_execute_process`'s `finally` under the manager's lock, but
`list()` takes no lock, so this window opens on every normal completion. It is the same
defect as the crash case and needs no crash to reach.

#### Scenario: Single-flight ignores a hidden envelope

- **WHEN** an envelope appears in `jobs_dir` while the manager is running and no job is active
- **THEN** the manager accepts a new job rather than reporting a conflict

`_active` is the site that decides single-flight. It falls through on an envelope today only
because an envelope answers `job.get("status")` with `None` — an accident of the envelope's
shape, not a rule. This scenario states the rule, so a later envelope carrying a `status` key
cannot silently wedge the console into permanent conflict.

### Requirement: An orphaned result envelope is swept at startup

`EvaluationJobManager` SHALL delete every `.<uuid>.result.json` file present in `jobs_dir` when it is constructed, and SHALL do so after the pass that marks stale jobs interrupted.

An envelope outlives its job when the process dies between the worker writing it and the
cleanup in `_execute_process` (`src/evaluation/qa/jobs.py:245-285`) — a SIGKILL during a
redeploy, or an OOM kill. `jobs_dir` is a host-mounted volume, so the file then persists
across every restart. The restart pass does not remove it: `_interrupt_stale_jobs`
(`src/evaluation/qa/jobs.py:59-73`) only rewrites files carrying an active `status`, and an
envelope carries no `status`. Recovery today is a human finding and deleting a hidden file.

A manager that has just been constructed holds no futures and no subprocesses, so no worker
it owns can be writing an envelope. Every envelope on disk at that instant therefore belongs
to a process that is gone. That is what makes the sweep safe without any timestamp check or
cross-reference against job records.

The ordering is load-bearing. The pass that marks stale jobs interrupted is what gives up on
those jobs; sweeping first would delete the result of a job the manager still records as
running, leaving the directory briefly describing a state that never existed.

The sweep SHALL match the exact name shape the manager writes, with the middle segment
parsed as a UUID. `jobs_dir` is a directory a human can reach, and a startup routine that
deletes files it did not create is a worse failure than the one it fixes.

#### Scenario: A stranded envelope is gone after a restart

- **WHEN** a manager is constructed over a `jobs_dir` holding `.<uuid>.result.json` and no active job
- **THEN** that file no longer exists
- **AND** a listing taken afterwards raises nothing

#### Scenario: The sweep does not delete files it did not write

- **WHEN** a manager is constructed over a `jobs_dir` also holding `.notes.result.json`, `.notes.json`, and `notes.json`
- **THEN** all three of those files still exist
- **AND** any `.<uuid>.result.json` beside them is gone

`.notes.result.json` is the case that decides the shape check rather than a looser dot-file
or `*.result.json` match: it is what an operator's own file in a host-mounted directory looks
like, and only the UUID parse tells it apart from the manager's own.

#### Scenario: A job interrupted by the same restart keeps its record

- **WHEN** a manager is constructed over a `jobs_dir` holding a record with status `running` and that job's envelope
- **THEN** the record reads `interrupted`
- **AND** the envelope is gone

### Requirement: A listing skips a file that is not a job record

`list()` SHALL skip any file in `jobs_dir` whose contents are not a mapping carrying an `"id"` key, in the same way it already skips a file that does not parse as JSON.

`list()`'s caller subscripts `job["id"]` without a guard
(`src/evaluation/qa/console.py:150-154`), and the route behind it is the one route in its
blueprint with no exception handler around its service calls
(`src/interfaces/chat_app/evaluation_routes.py:154-167`), so any `KeyError` there answers the
whole console page with 500.

This is defence in depth and is deliberately redundant with the two requirements above. Those
two remove the only known writer of an id-less file into `jobs_dir`; this one removes the
consequence, so a future writer costs one missing row instead of the console.

The method's contract already is "skip what is not a job record" — a file that fails to parse
as JSON is caught and skipped at `src/evaluation/qa/jobs.py:388-391`. A file that parses but
carries no identity is the same category of thing. Recorded against it: a silent skip can
hide a real defect. It is accepted because the alternative it replaces is a dead page rather
than a wrong answer.

#### Scenario: A record with no id is omitted rather than fatal

- **WHEN** `jobs_dir` holds a plainly-named `.json` file whose contents are a mapping with no `"id"` key, beside one valid job record
- **THEN** `list()` returns exactly the one job record
- **AND** `EvaluationConsoleService.list_jobs()` returns without raising

#### Scenario: Valid records are unaffected by the guard

- **WHEN** `jobs_dir` holds only well-formed job records
- **THEN** `list()` returns all of them, newest first
