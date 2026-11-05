## 1. Release a job record that nothing owns (fasrc/archi#751)

- [x] 1.1 Red then green in one task (do not commit a red state). Write Appendix A to `/tmp/751-A.diff` and run `git apply -p1 /tmp/751-A.diff` from the repository root. It adds three helpers (`_process_request`, `_fail_job_writes_after_the_first`, `_start_stuck_process_job`) and nine tests to `tests/unit/evaluation/qa/test_jobs_history.py`, ABOVE the last test (`test_the_sweep_keeps_a_uuid_spelled_differently_than_the_manager_writes_it`). Check that the last test keeps all its assertions. Run `python -m pytest tests/unit/evaluation/qa/test_jobs_history.py -q`. Expect exactly `6 failed, 60 passed`. The six red tests each fail with `JobConflictError`: `test_a_stuck_process_job_does_not_block_the_next_job_once_writes_recover`, `test_while_job_writes_fail_a_stuck_job_lets_start_raise_the_write_error`, `test_a_stuck_atom_job_reads_failed_and_does_not_block_the_next_job`, `test_a_job_whose_final_write_fails_does_not_block_the_next_job`, `test_a_failed_cancellation_does_not_leave_its_job_stuck` and `test_a_failed_cancellation_before_start_does_not_leave_its_job_stuck`. Three new tests pass on the old code because they are guards (design decisions 2 to 4): `test_overlapping_cancels_keep_the_job_blocking_until_the_last_one_ends`, `test_a_stuck_job_whose_process_is_still_tracked_keeps_blocking_until_canceled` and `test_an_active_record_with_a_non_string_id_still_blocks`. Then write Appendix B to `/tmp/751-B.diff` and `git apply -p1` it. It changes `src/evaluation/qa/jobs.py`: a `Counter` of cancel calls, `_settle_ownerless()` called from `_active()`, and a new `cancel()` wrapper around the old body, renamed `_cancel()` and otherwise unchanged. It keeps `future.result(timeout=5)` for now. Re-run the file: expect exactly `66 passed`. If a total differs, read the failure before you change anything; on a loaded host the older process tests can time out (design, Risks). Run `bash scripts/gate.sh` in the foreground, never in the background (green, patch coverage at least 80%), and commit.
- [x] 1.2 Red then green in one task (do not commit a red state). Write Appendix C to `/tmp/751-C.diff` and `git apply -p1` it. It adds `test_cancel_of_a_stuck_process_job_returns_canceled_without_raising` and `test_cancel_of_a_stuck_process_job_still_blocks_start_until_it_ends` above the last test. Run the file: expect exactly `2 failed, 66 passed`, both with `OSError: disk full` raised out of `cancel()`. Write Appendix D to `/tmp/751-D.diff` and `git apply -p1` it: in `_cancel()`, `future.result(timeout=5)` becomes `future.exception(timeout=5)` with a two-line comment. Re-run the file: expect exactly `68 passed`. Then run `python -m pytest tests/unit/evaluation/qa/test_jobs_history.py -q -k "stuck or write_fail or final_write or failed_cancellation or overlapping_cancels or non_string_id or terminates_running_evaluation_process"` (expect `12 passed`). Run `bash scripts/gate.sh` in the foreground (green, patch coverage at least 80%) and commit.

## 2. Docs

- [x] 2.1 Write Appendix E to `/tmp/751-E.diff` and `git apply -p1` it. It adds one paragraph to `docs/docs/evaluation.md`, at the end of the section "The console says another job is active", after the paragraph that ends "marked `interrupted`." and before "For exact command flags". Do not change other text. Run `bash scripts/gate.sh` in the foreground (green) and commit.

## 3. Publish

- [x] 3.1 Tick this box, commit the tick through the hook, then push and open the PR, so the push carries every commit. Push with `git push -u origin fix/issue-751-job-write-failure-recovery`. Write the PR body to a file under `/tmp` (never under the repository) and open the PR with `gh pr create --repo fasrc/archi --base dev --title "fix(qa): release an evaluation job that no worker or cancel owns (#751)" --body-file <that file>`. Put `Closes #751` in the PR **body**, not the title. In the body, state the defect (a worker whose job-file write raises leaves an active record that blocks every new job until a restart, and `cancel()` raises the dead worker's error), the ownership rule (future done, no cancel call in progress, no tracked process), the `cancel()` wrapper with its per-job count, the `future.exception` change, the eleven tests (eight red on `origin/dev`, three guards), and the six broken variants from the appendix table, each with the test that catches it. Name the docs paragraph. No `Co-Authored-By` trailer. Do not merge.

## 4. Post-PR check findings (2026-11-04 wrap-up)

The post-PR check of PR #807 confirmed these two findings. Do 4.1, then 4.2. Do not change anything else: two more findings (a paused job left over from a restart, and Cancel on a scored run) wait for a human decision and are listed in the PR body.

- [x] 4.1 Pin the rule "a failed settle write does not raise" (spec delta; design decision 4). No test catches the removal of the `try`/`except` in `_settle_ownerless`, because `_fail_job_writes_after_the_first` fails every write and so `start()` raises the same `OSError` either way. In `tests/unit/evaluation/qa/test_jobs_history.py`, add the test below directly ABOVE the last test (`test_the_sweep_keeps_a_uuid_spelled_differently_than_the_manager_writes_it`), with two blank lines before and after it. Do not change other lines and do not change `src/`. Then run `python -m pytest tests/unit/evaluation/qa/test_jobs_history.py -q -k failed_settle_write`: expect `1 passed`. Red check (this test guards existing code): in `src/evaluation/qa/jobs.py`, `_settle_ownerless`, replace the four lines `try:` / `write_json(self._path(job_id), job)` / `except Exception:` / `pass` with the one line `write_json(self._path(job_id), job)`, run the same command, and expect `1 failed` with `OSError: settle write failed`. Then run `git checkout -- src/evaluation/qa/jobs.py` and confirm `git diff -- src/` prints nothing. Run the whole file: expect exactly `69 passed`. Tick this box, `git add` the test file and this `tasks.md`, run `bash scripts/gate.sh` in the foreground (green), and commit as `test(qa): pin that a failed settle write does not raise (#751)` with the red line in the commit body. Push with `git push origin fix/issue-751-job-write-failure-recovery`.

  ```python
  def test_a_failed_settle_write_does_not_block_the_next_job(monkeypatch, tmp_path):
      manager = EvaluationJobManager(tmp_path)
      stuck, writes = _start_stuck_process_job(monkeypatch, manager, tmp_path)
      writes["failing"] = False
      stuck_name = f"{stuck['id']}.json"
      recovered_write_json = jobs_module.write_json

      def write_json(path, payload):
          if path.name == stuck_name:
              raise OSError("settle write failed")
          recovered_write_json(path, payload)

      monkeypatch.setattr(jobs_module, "write_json", write_json)

      next_job = manager.start("generate_atoms", lambda: {})

      assert manager.wait(next_job["id"], timeout=JOB_WAIT_TIMEOUT)["status"] == (
          "completed"
      )
      assert manager.get(stuck["id"])["status"] == "queued"
      manager.close()
  ```

- [x] 4.2 Docs: a **Cancel evaluation** also fails with HTTP 500 while job-file writes fail, because `_cancel()` first writes `cancel_requested`. In `docs/docs/evaluation.md`, in the paragraph that starts "A job can also keep reading", replace the sentence "While the jobs directory still refuses writes, a launch fails with HTTP 500 instead of 409, and the chat-app log names the write error." with "While the jobs directory still refuses writes, a launch or a **Cancel evaluation** fails with HTTP 500 instead of 409, and the chat-app log names the write error." Replace the last sentence, "For an evaluation that still reads `queued` or `running`, **Cancel evaluation** also ends it, as `canceled`.", with "When writes succeed again, **Cancel evaluation** also ends an evaluation that still reads `queued` or `running`, as `canceled`." Reflow only that paragraph to lines of at most 80 characters. Do not change other text. Tick this box, `git add` the docs file and this `tasks.md`, run `bash scripts/gate.sh` in the foreground (green), commit as `docs(qa): say that Cancel also fails while job writes fail (#751)`, and push with `git push origin fix/issue-751-job-write-failure-recovery`.

## Appendix (Loop 1 scratch evidence, 2026-11-04)

Loop 1 ran every stage below on a scratch copy of `origin/dev` `73104693`
(`git archive` of `src/`, `tests/` and `pyproject.toml`, under `/tmp`) with
`python -m pytest tests/unit/evaluation/qa/test_jobs_history.py -q -o addopts=""`,
one stage at a time:

| Stage | Result |
| --- | --- |
| `origin/dev` | `57 passed` |
| 1.1 red (Appendix A only) | `6 failed, 60 passed` |
| 1.1 green (A + B) | `66 passed` |
| 1.2 red (A + B + C) | `2 failed, 66 passed` |
| 1.2 green (A + B + C + D) | `68 passed` |

Six broken variants of the final code, each run with the task 1.2 `-k` selection:

| Variant | Tests that fail |
| --- | --- |
| no cancel count in `_settle_ownerless` | `test_job_manager_terminates_running_evaluation_process`, `test_overlapping_cancels_keep_the_job_blocking_until_the_last_one_ends`, `test_cancel_of_a_stuck_process_job_still_blocks_start_until_it_ends` |
| a set instead of a count (`pop` in the `finally`) | `test_overlapping_cancels_keep_the_job_blocking_until_the_last_one_ends` |
| no `finally` in `cancel()` | `test_a_failed_cancellation_does_not_leave_its_job_stuck`, `test_a_failed_cancellation_before_start_does_not_leave_its_job_stuck` |
| no process guard | `test_a_stuck_job_whose_process_is_still_tracked_keeps_blocking_until_canceled` |
| no `future.cancelled()` check | `test_a_failed_cancellation_before_start_does_not_leave_its_job_stuck` |
| no string check on the id | `test_an_active_record_with_a_non_string_id_still_blocks` |

The 1.2 green stage passed 15 of 15 repeats of the `-k` selection (`12 passed`).
`black` 24.10.0 and `isort` left all staged files unchanged. Patches A to E apply in
order with `git apply -p1` on `origin/dev` `73104693`, and the result is identical to
the tested files. Copy the patches; do not rewrite them.

### Appendix A (task 1.1, tests)

````diff
--- a/tests/unit/evaluation/qa/test_jobs_history.py
+++ b/tests/unit/evaluation/qa/test_jobs_history.py
@@ -2326,6 +2326,388 @@
     manager.close()
 
 
+def _process_request(tmp_path):
+    return {
+        "operation": "composite",
+        "output_dir": str(tmp_path / "run"),
+        "dataset": str(tmp_path / "dataset.json"),
+        "agent_config": str(tmp_path / "config.yaml"),
+        "agent_spec": str(tmp_path / "agent.md"),
+        "evaluator_profile_path": str(tmp_path / "profile.yaml"),
+        "attempts": 1,
+        "run_workers": 1,
+        "score_workers": 1,
+    }
+
+
+def _fail_job_writes_after_the_first(monkeypatch):
+    """Fail every job-file write after the first (the ``queued`` write).
+
+    Set ``writes["failing"] = False`` to let writes succeed again. Counting
+    writes, not statuses, keeps the stub free of timing: the first write is
+    the one ``start``/``start_process`` makes under the lock before it submits
+    the worker.
+    """
+    production_write_json = jobs_module.write_json
+    writes = {"count": 0, "failing": True}
+
+    def write_json(path, payload):
+        writes["count"] += 1
+        if writes["failing"] and writes["count"] > 1:
+            raise OSError("disk full")
+        production_write_json(path, payload)
+
+    monkeypatch.setattr(jobs_module, "write_json", write_json)
+    return writes
+
+
+def _start_stuck_process_job(monkeypatch, manager, tmp_path):
+    """Leave a process job ``queued`` on disk with a worker that raised (#751).
+
+    The ``running`` write raises, the worker is reaped, and the ``failed``
+    write raises too, so the exception ends the executor future.
+    """
+    writes = _fail_job_writes_after_the_first(monkeypatch)
+
+    class _FakeProcess:
+        pid = 2**31 - 1
+
+    monkeypatch.setattr(jobs_module.subprocess, "Popen", lambda *a, **k: _FakeProcess())
+    monkeypatch.setattr(manager, "_terminate", lambda process: None)
+    stuck = manager.start_process(
+        _process_request(tmp_path),
+        context={"workspace_id": "run", "attempts": 1},
+    )
+    with pytest.raises(OSError, match="disk full"):
+        manager.wait(stuck["id"], timeout=JOB_WAIT_TIMEOUT)
+    assert manager.get(stuck["id"])["status"] == "queued"
+    return stuck, writes
+
+
+def test_a_stuck_process_job_does_not_block_the_next_job_once_writes_recover(
+    monkeypatch, tmp_path
+):
+    manager = EvaluationJobManager(tmp_path)
+    stuck, writes = _start_stuck_process_job(monkeypatch, manager, tmp_path)
+    writes["failing"] = False
+
+    next_job = manager.start("generate_atoms", lambda: {})
+
+    assert manager.wait(next_job["id"], timeout=JOB_WAIT_TIMEOUT)["status"] == (
+        "completed"
+    )
+    settled = manager.get(stuck["id"])
+    assert settled["status"] == "failed"
+    assert "disk full" in settled["error"]
+    assert settled["completed_at"] is not None
+    manager.close()
+
+
+def test_while_job_writes_fail_a_stuck_job_lets_start_raise_the_write_error(
+    monkeypatch, tmp_path
+):
+    manager = EvaluationJobManager(tmp_path)
+    stuck, _writes = _start_stuck_process_job(monkeypatch, manager, tmp_path)
+
+    with pytest.raises(OSError, match="disk full"):
+        manager.start("generate_atoms", lambda: {})
+
+    assert manager.get(stuck["id"])["status"] == "queued"
+    assert [job["id"] for job in manager.list()] == [stuck["id"]]
+    manager.close()
+
+
+def test_a_stuck_atom_job_reads_failed_and_does_not_block_the_next_job(
+    monkeypatch, tmp_path
+):
+    manager = EvaluationJobManager(tmp_path)
+    writes = _fail_job_writes_after_the_first(monkeypatch)
+    stuck = manager.start("generate_atoms", lambda: {"draft_id": "never"})
+    with pytest.raises(OSError, match="disk full"):
+        manager.wait(stuck["id"], timeout=JOB_WAIT_TIMEOUT)
+    assert manager.get(stuck["id"])["status"] == "queued"
+    writes["failing"] = False
+
+    next_job = manager.start("generate_atoms", lambda: {})
+
+    assert manager.wait(next_job["id"], timeout=JOB_WAIT_TIMEOUT)["status"] == (
+        "completed"
+    )
+    settled = manager.get(stuck["id"])
+    assert settled["status"] == "failed"
+    assert "disk full" in settled["error"]
+    manager.close()
+
+
+def test_a_job_whose_final_write_fails_does_not_block_the_next_job(
+    monkeypatch, tmp_path
+):
+    manager = EvaluationJobManager(tmp_path)
+    production_write_json = jobs_module.write_json
+    writes = {"failing": False}
+
+    def write_json(path, payload):
+        if writes["failing"]:
+            raise OSError("disk full")
+        production_write_json(path, payload)
+
+    class _FinishingProcess:
+        pid = 2**31 - 1
+
+        def wait(self, timeout=None):
+            # The worker exits; every write from here on fails.
+            writes["failing"] = True
+            return 0
+
+    monkeypatch.setattr(jobs_module, "write_json", write_json)
+    monkeypatch.setattr(
+        jobs_module.subprocess, "Popen", lambda *a, **k: _FinishingProcess()
+    )
+    job = manager.start_process(
+        _process_request(tmp_path),
+        context={"workspace_id": "run", "attempts": 1},
+    )
+    with pytest.raises(OSError, match="disk full"):
+        manager.wait(job["id"], timeout=JOB_WAIT_TIMEOUT)
+    assert manager.get(job["id"])["status"] == "running"
+    writes["failing"] = False
+
+    next_job = manager.start("generate_atoms", lambda: {})
+
+    assert manager.wait(next_job["id"], timeout=JOB_WAIT_TIMEOUT)["status"] == (
+        "completed"
+    )
+    settled = manager.get(job["id"])
+    assert settled["status"] == "failed"
+    assert "disk full" in settled["error"]
+    manager.close()
+
+
+def test_a_failed_cancellation_does_not_leave_its_job_stuck(monkeypatch, tmp_path):
+    manager = EvaluationJobManager(tmp_path)
+    release = threading.Event()
+
+    class _BlockingProcess:
+        pid = 2**31 - 1
+        returncode = None
+
+        def poll(self):
+            return self.returncode
+
+        def wait(self, timeout=None):
+            release.wait(JOB_WAIT_TIMEOUT)
+            self.returncode = -15
+            return self.returncode
+
+    monkeypatch.setattr(
+        jobs_module.subprocess, "Popen", lambda *a, **k: _BlockingProcess()
+    )
+    monkeypatch.setattr(manager, "_terminate", lambda process: release.set())
+    job = manager.start_process(
+        _process_request(tmp_path),
+        context={"workspace_id": "run", "attempts": 1},
+    )
+    deadline = time.monotonic() + JOB_WAIT_TIMEOUT
+    while manager.get(job["id"])["status"] != "running":
+        assert time.monotonic() < deadline
+        time.sleep(0.01)
+
+    def on_terminated(_job):
+        raise RuntimeError("canceled.json could not be written")
+
+    with pytest.raises(RuntimeError, match="canceled.json"):
+        manager.cancel(job["id"], on_terminated=on_terminated)
+    manager.wait(job["id"], timeout=JOB_WAIT_TIMEOUT)
+    assert manager.get(job["id"])["status"] == "cancel_requested"
+
+    next_job = manager.start("generate_atoms", lambda: {})
+
+    assert manager.wait(next_job["id"], timeout=JOB_WAIT_TIMEOUT)["status"] == (
+        "completed"
+    )
+    settled = manager.get(job["id"])
+    assert settled["status"] == "failed"
+    assert settled["error"] == "the job ended without a final status"
+    manager.close()
+
+
+def test_a_failed_cancellation_before_start_does_not_leave_its_job_stuck(
+    monkeypatch, tmp_path
+):
+    manager = EvaluationJobManager(tmp_path)
+    gate = threading.Event()
+    # Hold the single executor thread, so the job's future stays pending and
+    # cancel() takes its before-start path (future.cancel() returns True).
+    blocker = manager._executor.submit(gate.wait, JOB_WAIT_TIMEOUT)
+    popen_calls = []
+    monkeypatch.setattr(
+        jobs_module.subprocess, "Popen", lambda *a, **k: popen_calls.append(a)
+    )
+    job = manager.start_process(
+        _process_request(tmp_path),
+        context={"workspace_id": "run", "attempts": 1},
+    )
+
+    def on_terminated(_job):
+        raise RuntimeError("canceled.json could not be written")
+
+    try:
+        with pytest.raises(RuntimeError, match="canceled.json"):
+            manager.cancel(job["id"], on_terminated=on_terminated)
+    finally:
+        gate.set()
+    blocker.result(timeout=JOB_WAIT_TIMEOUT)
+    assert manager._futures[job["id"]].cancelled()
+    assert manager.get(job["id"])["status"] == "cancel_requested"
+
+    next_job = manager.start("generate_atoms", lambda: {})
+
+    assert manager.wait(next_job["id"], timeout=JOB_WAIT_TIMEOUT)["status"] == (
+        "completed"
+    )
+    settled = manager.get(job["id"])
+    assert settled["status"] == "failed"
+    assert settled["error"] == "the job ended without a final status"
+    assert popen_calls == []
+    manager.close()
+
+
+def test_overlapping_cancels_keep_the_job_blocking_until_the_last_one_ends(
+    monkeypatch, tmp_path
+):
+    """One cancel() that raises must not release a job another cancel() owns.
+
+    This passes on the code before #751 too. It guards the per-job count of
+    cancel calls: a set would let the first call to end release the record
+    while the other call is still in its ``cancel_requested`` window.
+    """
+    manager = EvaluationJobManager(tmp_path)
+    release_process = threading.Event()
+    first_cancel_terminating = threading.Event()
+    let_first_cancel_finish = threading.Event()
+    terminate_calls = []
+
+    class _BlockingProcess:
+        pid = 2**31 - 1
+        returncode = None
+
+        def poll(self):
+            return self.returncode
+
+        def wait(self, timeout=None):
+            release_process.wait(JOB_WAIT_TIMEOUT)
+            self.returncode = -15
+            return self.returncode
+
+    def fake_terminate(process):
+        # cancel() calls this without the manager lock.
+        terminate_calls.append(process)
+        if len(terminate_calls) == 1:
+            first_cancel_terminating.set()
+            let_first_cancel_finish.wait(JOB_WAIT_TIMEOUT)
+        else:
+            release_process.set()
+
+    monkeypatch.setattr(
+        jobs_module.subprocess, "Popen", lambda *a, **k: _BlockingProcess()
+    )
+    monkeypatch.setattr(manager, "_terminate", fake_terminate)
+    job = manager.start_process(
+        _process_request(tmp_path),
+        context={"workspace_id": "run", "attempts": 1},
+    )
+    deadline = time.monotonic() + JOB_WAIT_TIMEOUT
+    while manager.get(job["id"])["status"] != "running":
+        assert time.monotonic() < deadline
+        time.sleep(0.01)
+
+    first_results = []
+    first_errors = []
+
+    def first_cancel():
+        try:
+            first_results.append(manager.cancel(job["id"]))
+        except BaseException as exc:
+            first_errors.append(exc)
+
+    first = threading.Thread(target=first_cancel)
+    first.start()
+    try:
+        assert first_cancel_terminating.wait(JOB_WAIT_TIMEOUT)
+
+        def on_terminated(_job):
+            raise RuntimeError("canceled.json could not be written")
+
+        with pytest.raises(RuntimeError, match="canceled.json"):
+            manager.cancel(job["id"], on_terminated=on_terminated)
+        manager.wait(job["id"], timeout=JOB_WAIT_TIMEOUT)
+
+        with pytest.raises(JobConflictError, match="already cancel_requested"):
+            manager.start("generate_atoms", lambda: {})
+    finally:
+        let_first_cancel_finish.set()
+        first.join(JOB_WAIT_TIMEOUT)
+
+    assert first_errors == []
+    assert [result["status"] for result in first_results] == ["canceled"]
+    manager.close()
+
+
+def test_a_stuck_job_whose_process_is_still_tracked_keeps_blocking_until_canceled(
+    monkeypatch, tmp_path
+):
+    """Never release a record while the manager still tracks its process.
+
+    This passes on the code before #751 too. It guards the ownership rule: a
+    worker that raised out of ``process.wait()`` left its process in the
+    table, so the job is not ownerless and only ``cancel()`` may end it.
+    """
+    manager = EvaluationJobManager(tmp_path)
+    terminated = []
+
+    class _UnwaitableProcess:
+        pid = 2**31 - 1
+
+        def wait(self, timeout=None):
+            raise RuntimeError("wait failed")
+
+    process = _UnwaitableProcess()
+    monkeypatch.setattr(jobs_module.subprocess, "Popen", lambda *a, **k: process)
+    monkeypatch.setattr(manager, "_terminate", terminated.append)
+    job = manager.start_process(
+        _process_request(tmp_path),
+        context={"workspace_id": "run", "attempts": 1},
+    )
+    with pytest.raises(RuntimeError, match="wait failed"):
+        manager.wait(job["id"], timeout=JOB_WAIT_TIMEOUT)
+    assert manager.get(job["id"])["status"] == "running"
+
+    with pytest.raises(JobConflictError, match="already running"):
+        manager.start("generate_atoms", lambda: {})
+
+    assert manager.cancel(job["id"])["status"] == "canceled"
+    assert terminated == [process]
+    manager.close()
+
+
+def test_an_active_record_with_a_non_string_id_still_blocks(tmp_path):
+    """A hand-edited job file must not crash the ownership check.
+
+    This passes on the code before #751 too. ``jobs_dir`` is a host folder a
+    person can edit, and an id that cannot be a dict key must keep the old
+    behavior (a conflict), not raise ``TypeError``.
+    """
+    manager = EvaluationJobManager(tmp_path)
+    write_json(
+        tmp_path / f"{uuid.uuid4()}.json",
+        {"id": ["not", "a", "string"], "kind": "evaluation", "status": "queued"},
+    )
+
+    with pytest.raises(JobConflictError, match="already queued"):
+        manager.start("generate_atoms", lambda: {})
+    manager.close()
+
+
 def test_the_sweep_keeps_a_uuid_spelled_differently_than_the_manager_writes_it(
     tmp_path,
 ):
````

### Appendix B (task 1.1, `src/evaluation/qa/jobs.py`)

````diff
--- a/src/evaluation/qa/jobs.py
+++ b/src/evaluation/qa/jobs.py
@@ -8,6 +8,7 @@
 import threading
 import time
 import uuid
+from collections import Counter
 from concurrent.futures import Future, ThreadPoolExecutor
 from enum import Enum
 from pathlib import Path
@@ -46,6 +47,9 @@
         )
         self._futures: Dict[str, Future] = {}
         self._processes: Dict[str, subprocess.Popen] = {}
+        # cancel() calls in progress, per job id (#751). A count, not a set:
+        # two overlapping cancels of one job must not release it early.
+        self._cancel_calls: Counter[str] = Counter()
         self._interrupt_stale_jobs()
         self._sweep_orphan_results()
 
@@ -98,9 +102,43 @@
                 JobStatus.RUNNING.value,
                 JobStatus.CANCEL_REQUESTED.value,
             }:
+                if self._settle_ownerless(job):
+                    continue
                 return job
         return None
 
+    def _settle_ownerless(self, job: Dict[str, Any]) -> bool:
+        """Return True when nothing owns an active record any more (#751).
+
+        A worker that raised (for example, every job-file write failed) leaves
+        its record ``queued``, ``running`` or ``cancel_requested`` with no
+        thread to finish it. Such a record must not block new jobs. Try once
+        to record it as ``failed``; if the write fails too, the next call
+        tries again.
+        """
+        job_id = job.get("id")
+        if not isinstance(job_id, str):
+            return False
+        future = self._futures.get(job_id)
+        if (
+            future is None
+            or not future.done()
+            or self._cancel_calls[job_id] > 0
+            or job_id in self._processes
+        ):
+            return False
+        error = None if future.cancelled() else future.exception()
+        job["status"] = JobStatus.FAILED.value
+        job["completed_at"] = utc_now()
+        job["error"] = (
+            str(error) if error is not None else "the job ended without a final status"
+        )
+        try:
+            write_json(self._path(job_id), job)
+        except Exception:
+            pass
+        return True
+
     def start(
         self,
         kind: str,
@@ -371,6 +409,23 @@
         *,
         on_terminated: Optional[Callable[[Dict[str, Any]], None]] = None,
     ) -> Dict[str, Any]:
+        # While this call runs, its record keeps blocking new jobs, also after
+        # the worker future is done (#751).
+        with self._lock:
+            self._cancel_calls[job_id] += 1
+        try:
+            return self._cancel(job_id, on_terminated)
+        finally:
+            with self._lock:
+                self._cancel_calls[job_id] -= 1
+                if self._cancel_calls[job_id] <= 0:
+                    del self._cancel_calls[job_id]
+
+    def _cancel(
+        self,
+        job_id: str,
+        on_terminated: Optional[Callable[[Dict[str, Any]], None]],
+    ) -> Dict[str, Any]:
         with self._lock:
             job = self.get(job_id)
             if job["kind"] != "evaluation":
````

### Appendix C (task 1.2, tests)

````diff
--- a/tests/unit/evaluation/qa/test_jobs_history.py
+++ b/tests/unit/evaluation/qa/test_jobs_history.py
@@ -2708,6 +2708,44 @@
     manager.close()
 
 
+def test_cancel_of_a_stuck_process_job_returns_canceled_without_raising(
+    monkeypatch, tmp_path
+):
+    manager = EvaluationJobManager(tmp_path)
+    stuck, writes = _start_stuck_process_job(monkeypatch, manager, tmp_path)
+    writes["failing"] = False
+
+    canceled = manager.cancel(stuck["id"])
+
+    assert canceled["status"] == "canceled"
+    assert manager.get(stuck["id"])["status"] == "canceled"
+    next_job = manager.start("generate_atoms", lambda: {})
+    assert manager.wait(next_job["id"], timeout=JOB_WAIT_TIMEOUT)["status"] == (
+        "completed"
+    )
+    manager.close()
+
+
+def test_cancel_of_a_stuck_process_job_still_blocks_start_until_it_ends(
+    monkeypatch, tmp_path
+):
+    manager = EvaluationJobManager(tmp_path)
+    stuck, writes = _start_stuck_process_job(monkeypatch, manager, tmp_path)
+    writes["failing"] = False
+    terminal_callback_statuses = []
+
+    def on_terminated(terminated_job):
+        terminal_callback_statuses.append(terminated_job["status"])
+        with pytest.raises(JobConflictError, match="already cancel_requested"):
+            manager.start("generate_atoms", lambda: {})
+
+    canceled = manager.cancel(stuck["id"], on_terminated=on_terminated)
+
+    assert terminal_callback_statuses == ["cancel_requested"]
+    assert canceled["status"] == "canceled"
+    manager.close()
+
+
 def test_the_sweep_keeps_a_uuid_spelled_differently_than_the_manager_writes_it(
     tmp_path,
 ):
````

### Appendix D (task 1.2, `src/evaluation/qa/jobs.py`)

````diff
--- a/src/evaluation/qa/jobs.py
+++ b/src/evaluation/qa/jobs.py
@@ -451,7 +451,9 @@
         if process is not None:
             self._terminate(process)
         elif future is not None:
-            future.result(timeout=5)
+            # Wait for the worker to end, but do not raise its error: a worker
+            # that raised has nothing left to stop (#751).
+            future.exception(timeout=5)
 
         with self._lock:
             terminal = self.get(job_id)
````

### Appendix E (task 2.1, `docs/docs/evaluation.md`)

````diff
--- a/docs/docs/evaluation.md
+++ b/docs/docs/evaluation.md
@@ -1369,6 +1369,15 @@
 the service restarted, refresh the catalog and verify that the old job was
 marked `interrupted`.
 
+A job can also keep reading `queued`, `running` or `cancel_requested` after its
+worker stopped, for example when the console could not write the job file on a
+full disk. Such a job does not block new launches. The next launch records it as
+`failed`, with the error that stopped its worker when there was one, and then
+starts. While the jobs directory still refuses writes, a launch fails with HTTP
+500 instead of 409, and the chat-app log names the write error. For an evaluation
+that still reads `queued` or `running`, **Cancel evaluation** also ends it, as
+`canceled`.
+
 For exact command flags, see [`archi qa` in the CLI
 reference](cli_reference.md#archi-qa). For deployment-level paths and
 authentication settings, see [Configuration](configuration.md).
````
