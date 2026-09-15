# Fail the evaluation console closed on a storage error

## Why

`build_evaluation_service` is the seam that decides whether the evaluation console exists,
and it already fails closed on both config refusals: a missing `agent_config_path` and the
live-config identity each log an error and return `None`
(`src/interfaces/chat_app/evaluation_console.py:85` and `:93`). A storage error does not.
The service construction at `src/interfaces/chat_app/evaluation_console.py:103` carries no
exception handler, and that constructor touches disk three times:

- `EvaluationCatalog.__init__` runs five `mkdir(parents=True, exist_ok=True)` calls
  (`src/evaluation/qa/catalog.py:261-268`).
- `EvaluationHistory.__init__` mkdirs `runs_dir` (`src/evaluation/qa/history.py:53`).
- `EvaluationJobManager.__init__` mkdirs `jobs_dir` and then sweeps stale job files
  (`src/evaluation/qa/jobs.py:42-49`). The sweep writes: `write_json`
  (`src/evaluation/qa/jobs.py:73`) reaches `_atomic_write`, which raises a raw `OSError`.

`app.py` calls the seam bare inside `FlaskAppWrapper.__init__`
(`src/interfaces/chat_app/app.py:2868`), so any of those `OSError`s ends the app process.
An unwritable or mistyped `evaluations.root` — a read-only bind mount, rootless-podman or
NFS ownership on the auto-created host directory, a typo in an override — crash-loops the
whole chat container. Chat goes down for a console that is off by default.

The module docstring already promises the opposite: "the console turns itself off while chat
stays up" (`src/interfaces/chat_app/evaluation_console.py:75-76`). So does the capability
spec, whose refusal scenario ends "the chat app stays up, and the console stays off"
(`openspec/specs/qa-evaluation-trial/spec.md:105-114`). Both statements are true of the two
config refusals and false of a storage failure.

This blocks the enable-on-dev step of #320. Turning `evaluations.enabled: true` on with a
bad root takes dev chat down instead of leaving the console off.

## What Changes

- `build_evaluation_service` wraps the `EvaluationConsoleService(...)` construction in
  `try`/`except OSError`, logs one error that names the root path and the failure, and
  returns `None`. The console is off; chat stays up.
- The net catches `OSError` and nothing wider. A wider net would swallow a programming
  error — a `TypeError` from a changed constructor signature, for one — and report it to the
  operator as a disabled console. A test pins that boundary.
- A corrupt job file needs no new handling, and a test records why. `_interrupt_stale_jobs`
  already catches `ValueError` and continues (`src/evaluation/qa/jobs.py:62-64`), and
  `read_json` converts an unreadable file's `OSError` into that same `ValueError`
  (`src/evaluation/qa/artifacts.py:176-177`). This resolves plan item 3 of issue #328: no
  second net, and no second `except` clause.
- The module docstring names the storage case alongside the two config refusals, so the
  promise on line 75 lists all three ways the console turns itself off.
- Four tests in `tests/unit/test_evaluation_console.py`: the mkdir failure, a failure raised
  from the stale-job sweep's write, the corrupt-job-file regression, and the
  not-an-`OSError` boundary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `qa-evaluation-trial`: the requirement "Evaluations console behind a config toggle" gains
  the storage case. The capability is archived under `openspec/specs/qa-evaluation-trial/`
  (`openspec/changes/archive/2026-08-21-port-live-eval-trial/`), so this delta modifies the
  requirement rather than adding one.

## Impact

- `src/interfaces/chat_app/evaluation_console.py` — one `try`/`except` around an existing
  return, one log call, and the docstring.
- `tests/unit/test_evaluation_console.py` — four added tests. The 14 existing tests are
  unmodified. `test_evaluation_service_uses_deployment_defaults`
  (`tests/unit/test_evaluation_console.py:40`) patches the factory, so it never touches
  disk and the new net does not change its result.
- `src/interfaces/chat_app/app.py` is **not** edited. The unit suite does not import it, so
  the fix belongs in the seam; the call site stays thin and unchanged.
- `src/evaluation/qa/**` is **not** edited. The constructors are correct to raise; the seam
  is where the decision to disable the console lives.
- Unblocks the enable-on-dev step of #320.
