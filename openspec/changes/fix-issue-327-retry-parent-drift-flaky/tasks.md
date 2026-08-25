# Tasks — accept a crashed attempt under a post-run live-validation stamp

Every checkbox below is one loop turn and ends **green and committed**. Write the failing
test, watch it fail, write the smallest fix, run the gate, commit. Never end a task with the
suite red, and never use `--no-verify`.

Four standing notes for every task:

- **Scope.** The only source file this change may edit is `src/evaluation/qa/workspace.py`,
  and inside it only the post-run branch of `RetryParentStore._load`
  (`src/evaluation/qa/workspace.py:245-259`). Do not edit `workflow.py`, `scoring.py`,
  `phases.py`, `console.py`, or `src/interfaces/chat_app/evaluation_routes.py`. Nothing in
  the deployment, CI, or control-plane trees is in scope for this change.
- **Fixtures come from the pipeline.** Build every workspace by driving
  `QAWorkflow().composite` with the fakes already in
  `tests/unit/evaluation/qa/test_live_workflow.py` (`SequenceInvoker`, `EvaluatorFactory`,
  `AgentFactory`, `_dataset`, `_run`). Never hand-write `evaluation_results.jsonl`. A
  corruption case is made by mutating a pipeline-built workspace and re-hashing it.
- **Commands.** The gate is the project gate command from `CLAUDE.md`, run with
  `PATH=/home/austin/miniforge3/envs/archi/bin:$PATH`. The fast loop is
  `/home/austin/miniforge3/envs/archi/bin/python -m pytest tests/unit/evaluation/qa/test_live_workflow.py tests/unit/evaluation/qa/test_workspace.py tests/unit/evaluation/qa/test_workflow.py -q`.
- **Format before you stage.** The pre-commit hook's black rewrites files after staging, so
  run black and isort, then `git add`, then commit, and confirm `git status` is empty.

## 1. Admit the drift-plus-flaky workspace

- [ ] 1.1 `model: opus` — In `tests/unit/evaluation/qa/test_live_workflow.py`, add a
      `FailingAgentFactory` beside `AgentFactory`
      (`tests/unit/evaluation/qa/test_live_workflow.py:58-73`) whose `run` raises
      `RuntimeError("agent exploded")` for a named question and returns `"agent answer"`
      otherwise. Add a helper that runs `QAWorkflow().composite` over
      `_dataset(..., include_static=True)` with that agent monkeypatched over
      `workflow_module.ArchiAgentRuntime`, and a `SequenceInvoker` whose pre-run value is
      `{"value": 7, "revision": "r1"}` and post-run value is `{"value": 8, "revision": "r2"}`
      — a post-run drift on the live item, whose only attempt crashed. RED test: assert
      `read_json(run_dir / "manifest.json")["status"] == "scored"`, then assert
      `EvaluationWorkspace.open_retry_parent(run_dir)` returns a store. Watch it fail with
      `parent run answer and live-validation phase disagree`. Then fix the post-run branch of
      `_load` (`src/evaluation/qa/workspace.py:245-259`): when the phase is `post_run`,
      accept an answer status in `{AnswerStatus.ANSWER_READY.value,
      AnswerStatus.EXECUTION_FAILED.value}` and reject a missing row or any other status;
      leave the `pre_run` branch exactly as it is. Close the store in the test. Gate green;
      commit.
- [ ] 1.2 `model: sonnet` — Regression guard, no source change expected: on that same
      workspace assert `QAWorkflow().retry_plan(run_dir)` returns a plan whose
      `live_validation_attempt_count` covers the crashed attempt and whose
      `execution_attempt_count` is `0` for it. **This passes once 1.1 lands — that is the
      point of it. Do not contrive a failure first.** It pins the retry kind, which is what
      holds the attempt behind a fresh pre-run check
      (`src/evaluation/qa/workflow.py:952-966`). Gate green; commit.
- [ ] 1.3 `model: opus` — Grandchild case. Drive `QAWorkflow().retry(parent, successor)` on
      the 1.1 workspace with the oracle still drifted (extend the `SequenceInvoker` values so
      the retry's fresh pre-run check also mismatches the prepared baseline), then assert
      `EvaluationWorkspace.open_retry_parent(successor)` accepts the successor. This covers
      the second producer (`src/evaluation/qa/workflow.py:1140-1164`), which re-stamps
      `live_validation_failed` over the same slots. If the retry needs the successor to reach
      `scored` before `open_retry_parent` will look at it, score it through the same workflow
      object rather than editing the manifest. Gate green; commit.

## 2. Keep the rejections that matter

- [ ] 2.1 `model: opus` — Add a `_repack(run_dir, **rows)` helper to the same test file that
      rewrites named JSONL artifacts of a pipeline-built workspace and then re-writes
      `manifest.json` with hashes recomputed by
      `src.evaluation.qa.artifacts.artifact_hashes`, so `verify_hashes`
      (`src/evaluation/qa/workspace.py:114-132`) still passes and `_load` is what does the
      rejecting. Two tests, both expected to raise `ValueError`: (a) delete the crashed
      attempt's row from `answers.jsonl` — a post-run stamp over no answer; (b) take a
      workspace whose live item failed its **pre-run** check and add an `answer_ready` answer
      row for one of its slots. Assert the message for each. Watch (a) fail if 1.1's fix
      accepted a missing row; make it green. Gate green; commit.
- [ ] 2.2 `model: sonnet` — Guard the corruption checks 1.1 must not have loosened: on a
      pipeline-built workspace, rewrite one attempt's `agent_config_sha256` to a different
      64-hex value, re-hash with the 2.1 helper, and assert `open_retry_parent` raises with
      `provenance`. Then rewrite one result's `attempt_id` to an ordinal the prepared
      membership does not contain and assert it raises with `identities`. Gate green; commit.

## 3. Prove the benchmark numbers did not move

- [ ] 3.1 `model: opus` — Scoring-unchanged test. Run the 1.1 shape twice: once with the
      crashing agent, once with the plain `AgentFactory`, both with the same drifted oracle
      sequence and the same dataset. Read `summary.json` from each and assert the live item's
      row is equal across the two runs in `requested`, `quality_k`, `scored_attempts`,
      `execution_failed_attempts`, and `live_validation_failed_attempts`, and that
      `execution_failed_attempts` is `0` in both. This is the executable form of acceptance
      criterion 3 and of the second spec requirement: it fails the moment anyone fixes this
      defect on the producer side instead. **Expect it to pass as written** — no source
      change belongs in this task. Gate green; commit.

## 4. Close out

- [ ] 4.1 `model: haiku` — Run the gate once more on the finished change and confirm it exits
      0 with patch coverage at or above 80%. Confirm `git status` is empty. Push with
      `git push -u origin fix/issue-327-retry-parent-drift-flaky` — the branch tracks
      `origin/dev`, so `-u` is required. Open the PR with
      `gh pr create --repo fasrc/archi --base dev`, put `closes #327` in the **body** (a
      closing keyword in the title does not link the issue), and stop. Do not merge.
