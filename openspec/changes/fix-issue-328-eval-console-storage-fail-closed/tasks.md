# Tasks — fail the evaluation console closed on a storage error

Every checkbox below is one loop turn and ends **green and committed**. Where a checkbox
says RED, write the failing test, watch it fail, write the smallest fix, run the gate, and
commit — all inside that one checkbox. Never end a task with the suite red, and never use
`--no-verify`.

Run the project gate before every commit — the command is in `CLAUDE.md` under "Gate", and
the loop's own prompt already runs it. On this host it needs the project interpreter on
`PATH`:

```
PATH=/home/austin/miniforge3/envs/archi/bin:$PATH
```

Focused run while working:

```
/home/austin/miniforge3/envs/archi/bin/python -m pytest tests/unit/test_evaluation_console.py -q
```

Three standing notes for every task:

- **Scope.** The only files this change edits are
  `src/interfaces/chat_app/evaluation_console.py` and
  `tests/unit/test_evaluation_console.py`. Do **not** edit
  `src/interfaces/chat_app/app.py` (the unit suite does not import it, so new lines there
  fail diff-cover — the seam exists for exactly this), and do **not** edit
  `src/evaluation/qa/**` (those constructors are correct to raise).
- **The 14 existing tests stay unmodified.** `test_evaluation_service_uses_deployment_defaults`
  (`tests/unit/test_evaluation_console.py:40`) patches the factory, so it never touches disk
  and the new guard cannot change its result. If any existing test needs an edit to pass,
  stop — the fix is wrong, not the test.
- **Log assertions.** The existing refusal tests assert an exact one-record level list
  (`assert [record.levelname for record in caplog.records] == ["ERROR"]`,
  `tests/unit/test_evaluation_console.py:85`). Copy that shape, so an extra log line is a
  failure rather than a silent pass.

## 1. Fail closed on a storage error

- [x] 1.1 RED, then GREEN. Add `test_evaluation_service_disables_on_an_unwritable_root` to
      `tests/unit/test_evaluation_console.py`. Build the blocker inside `tmp_path`: write a
      regular file at `tmp_path / "blocker"`, then set `evaluations.root` to
      `str(tmp_path / "blocker" / "evaluations")`. `mkdir(parents=True, exist_ok=True)` on
      that path raises `NotADirectoryError`, an `OSError` subclass — measured `[Errno 20] Not
      a directory` on 2026-08-25. Use a real `agent_config_path` under `tmp_path` and
      monkeypatch `LIVE_AGENT_CONFIG_PATH` the way
      `test_evaluation_service_accepts_a_distinct_existing_config`
      (`tests/unit/test_evaluation_console.py:172`) does, so the live-config refusal does not
      fire first and pass the test for the wrong reason. Assert three things:
      `build_evaluation_service(...) is None`, exactly one `ERROR` record, and the configured
      root string in `caplog.text`. Watch it fail — it fails by raising, not by returning a
      value, so confirm the failure is `NotADirectoryError` reaching the test and not an
      assertion error. Then wrap the `return EvaluationConsoleService(...)` statement at
      `src/interfaces/chat_app/evaluation_console.py:103` in `try` / `except OSError as exc`,
      log one `logger.error` naming the configured root and `exc`, and `return None`. Catch
      `OSError` only — not `Exception`. Gate, commit.

- [x] 1.2 Add `test_evaluation_service_disables_when_the_stale_job_sweep_cannot_write`. No
      production change: this proves the guard from 1.1 covers the *whole* construction, not
      just the first mkdir. Use a writable `evaluations.root` under `tmp_path`, then
      monkeypatch `write_json` in `src.evaluation.qa.jobs` to raise
      `OSError("read-only file system")`. `EvaluationJobManager.__init__` calls the stale-job
      sweep at `src/evaluation/qa/jobs.py:49`, which writes at
      `src/evaluation/qa/jobs.py:73` — so the sweep needs a job file to find: write one
      valid JSON job into `<root>/jobs/<uuid4>.json` with `"status": "queued"` first, or the
      sweep iterates nothing and never writes. Assert `build_evaluation_service(...) is None`
      and one `ERROR` record. Gate, commit.

## 2. Hold the two boundaries

- [x] 2.1 Add `test_evaluation_service_survives_a_corrupt_job_file`. This is the regression
      test for plan item 3 of issue #328, and it records the answer: a corrupt job file needs
      no net of its own, because `_interrupt_stale_jobs` already catches `ValueError` and
      continues (`src/evaluation/qa/jobs.py:62-64`), and `read_json` turns an unreadable file
      into that same `ValueError` (`src/evaluation/qa/artifacts.py:176-177`). Use a writable
      `evaluations.root` under `tmp_path`, write `not json at all` into
      `<root>/jobs/<uuid4>.json`, and assert `build_evaluation_service(...)` returns a
      service (not `None`) and logs no `ERROR`. No production change. Gate, commit.

- [x] 2.2 Add `test_evaluation_service_does_not_swallow_a_non_storage_error`. Patch
      `evaluation_console.EvaluationConsoleService` to raise
      `TypeError("unexpected keyword argument")`, copying the `patch.object` shape at
      `tests/unit/test_evaluation_console.py:42`, and assert `pytest.raises(TypeError)`
      around the `build_evaluation_service` call. This pins the `except OSError` boundary: a
      later widening to `except Exception` turns a real defect into a quietly disabled
      console, and this test fails when that happens. No production change. Gate, commit.

## 3. Make the promise match the code

- [ ] 3.1 Update the two docstrings in `src/interfaces/chat_app/evaluation_console.py` so the
      recorded promise covers three refusals rather than two. In the module docstring
      (lines 8-11), the sentence listing what `build_evaluation_service` refuses gains the
      storage case. In the function docstring, the paragraph ending "Each refusal logs an
      error and returns `None`. `app.py` calls this during init, so the console turns itself
      off while chat stays up." (lines 75-76) names the storage failure as one of those
      refusals, and says the net is `OSError` only and why: a wider net would report a
      programming error as a disabled console. Keep it to the two docstrings — no behaviour
      change in this task. Gate, commit.

## 4. Wrap up

- [ ] 4.1 Confirm the acceptance criteria of issue #328 in order. Run the focused file, then
      the full gate. Record in the commit message: the new test count (4), that the 14
      existing tests are unmodified (`git diff origin/dev -- tests/unit/test_evaluation_console.py`
      shows additions only), and the gate's patch-coverage number. Then push the branch with
      `git push -u origin fix/issue-328-eval-console-storage-fail-closed` and open the PR
      against `fasrc/archi:dev` with `closes #328` **in the body** (a closing keyword in the
      title does not link the issue). Do not merge.
