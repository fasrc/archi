# Tasks — hidden result envelopes never reach the job listing

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail, write the smallest fix, run `bash scripts/gate.sh`, commit. Never end a
task with the suite red, and never use `--no-verify`.

Four standing notes for every task:

- **Files.** Source changes go in `src/evaluation/qa/jobs.py` only. Tests go in
  `tests/unit/evaluation/qa/test_jobs_history.py` only. Do not edit
  `src/evaluation/qa/console.py`, `src/interfaces/chat_app/evaluation_routes.py`,
  `src/evaluation/qa/worker.py`, or any existing test.
- **Coverage.** `src/evaluation/qa/jobs.py` is under `--cov=src`, so every line you add
  reports to `diff-cover` and the patch must clear 80%. Each task's own test covers that
  task's lines — do not defer coverage to a later task.
- **Format.** Both files are black 24.10.0 clean and isort clean at `origin/dev` `44f90abc`
  (verified 2026-08-25). Run black **before** `git add`, not after, and confirm
  `git status` is empty once the commit lands.
- **Fixtures.** Use `tmp_path` and the `write_json` helper the test module already imports.
  Follow the shape at `tests/unit/evaluation/qa/test_jobs_history.py:415-425`
  (`test_job_manager_marks_stale_work_interrupted`) — a literal UUID string, a job record
  written straight to disk, then `EvaluationJobManager(tmp_path)`. Call `manager.close()`
  at the end of every test that constructs one. No network, no subprocess, no registry.

## 1. One helper owns the listing glob

**Ordering that matters in this group.** Write the envelope **after** constructing the
manager, not before. Section 2 adds a startup sweep that deletes envelopes found at
construction, so a test that drops one in first would stop testing the glob and start testing
the sweep. Writing it afterwards also matches how the file really appears: mid-run, while the
manager is alive. Section 2's tests do the opposite, deliberately.

- [ ] 1.1 `model: opus` — RED test: construct `EvaluationJobManager(tmp_path)`, then write a valid job
      record to `tmp_path / f"{job_id}.json"` and an envelope to
      `tmp_path / f".{uuid4()}.result.json"` holding `{"result": {"draft_id": "d"}}`. Assert
      `list()` returns exactly one record and that record's `"id"` is `job_id`. Then construct
      an `EvaluationConsoleService` over the same directory and assert `list_jobs()` raises
      nothing. Watch it fail — today `list()` returns two entries and `list_jobs()` raises
      `KeyError: 'id'`. Implement: add a private `_job_files()` returning
      `self.jobs_dir.glob("[!.]*.json")`, and call it from all three current glob sites
      (`src/evaluation/qa/jobs.py:60`, `:76`, `:387`). Do not change any other behaviour in
      those three methods. Gate green; commit.
- [ ] 1.2 `model: sonnet` — Regression guard for `_active` (`src/evaluation/qa/jobs.py:76`), the site
      that decides single-flight. Construct the manager, write an envelope into `tmp_path`
      afterwards, and assert `manager.start(...)` succeeds rather than raising
      `JobConflictError`. **This passes once 1.1 lands — that is the point of it. Do not
      contrive a failure first.** Because the envelope is written after construction, nothing
      in section 2 touches this test. Gate green; commit.

## 2. Sweep the orphans at startup

- [ ] 2.1 `model: opus` — RED test: write `.{orphan_id}.result.json` into `tmp_path` for a literal UUID
      `orphan_id`, construct `EvaluationJobManager(tmp_path)`, and assert the file no longer
      exists. Watch it fail. Implement: a private `_sweep_orphan_results()` that iterates
      `self.jobs_dir.glob(".*.result.json")`, parses the middle segment
      (`path.name[1:-len(".result.json")]`) with `uuid.UUID`, skips the file when the parse
      fails or the canonical string differs from that segment — mirroring the check in
      `_path` (`src/evaluation/qa/jobs.py:51-57`) — and otherwise calls the existing
      `_remove_result` static method (`src/evaluation/qa/jobs.py:286-291`). Call it from
      `__init__` on the line **after** `self._interrupt_stale_jobs()`
      (`src/evaluation/qa/jobs.py:48`); the ordering is a spec requirement, not a preference.
      Gate green; commit.
- [ ] 2.2 `model: sonnet` — RED test for the shape check: put `.notes.result.json`, `.notes.json`, and
      `notes.json` in `tmp_path` alongside one `.{orphan_id}.result.json`, construct the
      manager, and assert the first three still exist and the fourth is gone. Watch it fail if
      2.1's match was written as a bare dot-file or bare `*.result.json` sweep; make it green.
      `jobs_dir` is host-mounted, so this is the test that bounds a startup delete to the
      files this class wrote. Gate green; commit.
- [ ] 2.3 `model: sonnet` — RED-or-guard test for the ordering: write a record
      `{"id": job_id, "kind": "evaluation", "status": "running"}` **and** that job's own
      `.{job_id}.result.json`, construct the manager, then assert `manager.get(job_id)["status"]`
      is `"interrupted"` and the envelope is gone. Both halves must hold in the same
      construction. Gate green; commit.

## 3. A listing skips what is not a job record

- [ ] 3.1 `model: sonnet` — RED test: write a plainly-named `tmp_path / "stray.json"` holding
      `{"result": {"draft_id": "d"}}` — a mapping with no `"id"` — beside one valid job
      record. Assert `list()` returns exactly the one record and that
      `EvaluationConsoleService.list_jobs()` raises nothing. Watch it fail. Implement: in
      `list()`, after `read_json`, skip anything that is not a `dict` carrying `"id"`,
      immediately alongside the existing `except ValueError: continue`
      (`src/evaluation/qa/jobs.py:388-391`). Add a second assertion in the same test that two
      well-formed records are both returned, newest `created_at` first, so the guard is shown
      not to drop valid rows. Gate green; commit.

## 4. Close-out

- [ ] 4.1 `model: haiku` — Run `bash scripts/gate.sh` once more on the finished change and confirm it
      exits 0. Confirm `git status` is empty after the last commit. Push with
      `git push -u origin fix/issue-329-orphan-result-envelope` — the branch currently tracks
      `origin/dev`, so `-u` is required or the push retargets the trunk. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, and put `closes #329` in the **body**
      (a closing keyword in the title does not link the issue). Then stop. Do not merge.
