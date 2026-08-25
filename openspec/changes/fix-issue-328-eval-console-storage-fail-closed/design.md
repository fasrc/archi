# Design — fail the evaluation console closed on a storage error

## Where the net goes

The net wraps the whole `EvaluationConsoleService(...)` construction statement in
`build_evaluation_service` (`src/interfaces/chat_app/evaluation_console.py:103`), not the
individual constructors in `src/evaluation/qa/**`.

Two reasons, and the second is the load-bearing one.

The seam is the tested unit. `app.py` is not imported by the unit suite, which is why this
module exists at all (module docstring, lines 3-6; the pattern is `config_fingerprint.py`).
A fix inside `EvaluationCatalog` would be covered, but the *decision* under test here is
"does the console exist" — and that decision is only made in the seam.

One net covers three constructors and any fourth. `EvaluationConsoleService.__init__`
(`src/evaluation/qa/console.py:40-42`) builds a catalog, a history, and a job manager in
sequence, and all three touch disk:

| Constructor | Disk work | Anchor |
| --- | --- | --- |
| `EvaluationCatalog` | five `mkdir(parents=True, exist_ok=True)` | `src/evaluation/qa/catalog.py:261-268` |
| `EvaluationHistory` | one `mkdir` on `runs_dir` | `src/evaluation/qa/history.py:53` |
| `EvaluationJobManager` | `mkdir` on `jobs_dir`, then a stale-job sweep that **writes** | `src/evaluation/qa/jobs.py:42-49` |

The third row is why the net cannot be a single `try` around the catalog mkdir. The sweep
calls `write_json` (`src/evaluation/qa/jobs.py:73`), which reaches `_atomic_write` and
raises a raw `OSError`. A read-only mount that already holds a queued job file fails on the
write, well after every mkdir has succeeded. A net placed at the first mkdir would miss it.

## Why the net is `OSError` and nothing wider

`OSError` is the exact statement "the storage this console needs is not usable". Every way
the issue describes reaching this state raises it or a subclass: `PermissionError` on a
read-only bind mount or a wrong-owner host directory, `NotADirectoryError` on a root whose
parent is a regular file, `FileExistsError` on a root shadowed by a file.

A wider `except Exception` would also catch a programming error. If a constructor signature
changes and the seam passes a wrong keyword, the operator gets "evaluation console disabled"
and a log line about storage — for a bug that has nothing to do with storage, on a code path
CI would otherwise fail loudly. Silence there is worse than a crash, because a crash is
seen. Task 2.2 pins this with a test: a `TypeError` raised from the constructor still
propagates.

### What stays out of the net, on purpose

A wrong *type* in `evaluations.root` is not caught. `Path(evaluations_config.get("root",
DEFAULT_EVALUATION_ROOT))` is evaluated as an argument, so it sits inside the `try` block,
but `Path(5)` raises `TypeError`, not `OSError`. That is a config-schema problem, not a
storage problem, and it belongs with the other type validation in this function
(`agent_config_path` is already type-checked at line 84) or in config validation upstream.
Widening the net to reach it would re-open the boundary the paragraph above closes. It is
out of scope for #328, whose acceptance criteria name `OSError`.

## Plan item 3 of the issue: the corrupt-job-file question

Issue #328 asks whether a `ValueError` from an unreadable job file during the stale-job
sweep belongs in the same net. It does not, and the reason is that it cannot escape.

`_interrupt_stale_jobs` already catches `ValueError` from its `read_json` call and continues
to the next file (`src/evaluation/qa/jobs.py:62-64`). And `read_json` converts *both*
failure modes into that one `ValueError`: invalid JSON and an unreadable file alike
(`src/evaluation/qa/artifacts.py:176-177`). So corrupt state at boot is already survivable,
and no second `except` clause is needed.

That is a claim about code the fix does not touch, which is exactly the kind of claim that
rots. Task 2.1 turns it into a regression test: a corrupt job file in a writable root still
yields a working console. If someone later removes that `continue`, a test fails here rather
than the chat app crash-looping on a deployment.

## The test mechanism

Point `evaluations.root` at a path whose parent is a regular file, inside `tmp_path`:

```
root = tmp_path / "blocker" / "evaluations"   # tmp_path/blocker is a regular file
```

`mkdir(parents=True, exist_ok=True)` then raises `NotADirectoryError` — an `OSError`
subclass. Measured against the project interpreter on 2026-08-25: `[Errno 20] Not a
directory`.

This is better than the `chmod 0` option the issue's plan offers as an alternative. A
`chmod`ged directory is still writable by root, so that test needs a skip-if-root guard, and
a guarded test does not run in a root container — which is where this deployment actually
runs. The regular-file blocker fails for everyone, root included, and needs no guard.

For the sweep's write failure (task 1.2) a real read-only mount is not available in a unit
test, so the test patches `write_json` in the jobs module to raise `OSError`. That is a
mechanism substitution, and it is honest about what it proves: that an `OSError` raised
late in construction — after every mkdir has succeeded — is still caught. It does not prove
a read-only mount behaves that way; the anchor at `src/evaluation/qa/jobs.py:73` is the
evidence for that.

## The log line

One `logger.error` call, naming the configured root and the underlying failure. The root is
the field the operator has to change, so a message without it sends them reading code.
Level `ERROR` matches the two existing refusals (lines 85 and 93), and the existing tests
assert an exact one-record level list, so the new tests do the same.

The docstring on `build_evaluation_service` lists the refusals it makes. It gains the
storage case, so line 75's promise — "Each refusal logs an error and returns `None`" —
covers all three, not two of three.
