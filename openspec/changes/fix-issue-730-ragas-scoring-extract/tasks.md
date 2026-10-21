Rules for every task:
- The design of record is `openspec/changes/offload-ragas-judge-to-slurm/`. This change
  only drives PR 1 of it (issue #730). Read `design.md` here (D1–D7) before task 1.2.
- Write a failing test first and watch it fail, then write the minimum code, in the same
  task. Task 1.2 is the exception: it is a characterization test that passes on today's
  code.
- Run `bash scripts/gate.sh` before each commit. Commit only when it is green. Never use
  `--no-verify`. Add no `Co-Authored-By` or session trailers.
- Do not edit `tests/unit/test_benchmark_ragas_dialect.py`,
  `tests/unit/test_benchmark_resilience.py` or `tests/unit/test_benchmark_judge_identity.py`.
- Do not start group 2 of the parent change (`judge_mode`, bundle, publish), and do not
  change result JSON keys, `handle_results`, `ResultHandler`, `run()`, `_process_config`
  or `LocalProvider`.

## 1. Pin today's behavior (parent tasks 1.1 and 1.2)

- [x] 1.1 Run `black --check src/bin/service_benchmark.py` and
      `isort --check-only src/bin/service_benchmark.py`. In
      `openspec/changes/offload-ragas-judge-to-slurm/tasks.md`, tick task 1.1 and append
      the result to its line (expected: "black 24.10.0 and isort 6.0.1 report the file
      clean, so an in-place edit does not reflow it"; if either check fails, write the
      real output instead and STOP, with a note in `docs/questions.md`). Run
      `bash scripts/gate.sh` and commit when green (message: `record the black-seam check
      for service_benchmark`).

- [x] 1.2 Create `tests/unit/test_ragas_scoring_characterization.py` with the two cases
      of design D5, and nothing that imports `src.utils.ragas_scoring`. Run
      `python -m pytest -q tests/unit/test_ragas_scoring_characterization.py` and confirm
      both tests PASS on today's code. Then prove that the test pins the side effects:
      remove `callbacks=[recorder],` at `src/bin/service_benchmark.py:2269`, confirm the
      test FAILS, restore the line, and confirm
      `git diff --exit-code src/bin/service_benchmark.py` exits 0. Run
      `python -m pytest -q tests/unit/test_benchmark_ragas_dialect.py tests/unit/test_benchmark_resilience.py tests/unit/test_benchmark_judge_identity.py`
      and confirm all pass. Tick task 1.2 in the parent `tasks.md`. Run
      `bash scripts/gate.sh` and commit when green (message: `pin ragas scoring results
      with a characterization test`). From this commit on, never edit
      `tests/unit/test_ragas_scoring_characterization.py`.

## 2. Move the scoring (parent task 1.3)

- [ ] 2.1 Create `tests/unit/test_ragas_scoring.py` with the branch tests of design D7:
      one test for each branch of `build_ragas_judge_llm` (`openai`, `ollama`, `local`
      in both modes, `huggingface`, `anthropic`, `huit_bedrock`, default) and of
      `build_ragas_embeddings` (`openai`, `huggingface`, default), with stub
      constructors. Use unique test names. Run the file and confirm it FAILS (the module
      does not exist). Then, in the same task, create `src/utils/ragas_scoring.py` and
      make `Benchmarker.get_ragas_results`, `get_ragas_llm_evaluator` and
      `get_ragas_embedding_model` thin wrappers over it, as design D2, D3 and D4 say:
      inject the `service_benchmark` globals, keep the lazy ragas imports in the module,
      build the judge and the embeddings eagerly, and copy `scored` and `usage` from the
      outcome object in a `finally`. Import the pure helpers from
      `src/utils/benchmark_schema.py`; do not copy them. Remove imports from
      `service_benchmark.py` only if nothing else in the file uses them. Then run, and
      confirm all pass:
      `python -m pytest -q tests/unit/test_ragas_scoring.py tests/unit/test_ragas_scoring_characterization.py tests/unit/test_benchmark_ragas_dialect.py tests/unit/test_benchmark_resilience.py tests/unit/test_benchmark_judge_identity.py`.
      Confirm `git diff --exit-code HEAD -- tests/unit/test_ragas_scoring_characterization.py tests/unit/test_benchmark_ragas_dialect.py tests/unit/test_benchmark_resilience.py tests/unit/test_benchmark_judge_identity.py`
      exits 0. Tick task 1.3 in the parent `tasks.md`. Run `bash scripts/gate.sh`. If
      patch coverage is below 80%, read the `diff-cover` report and add tests for the
      uncovered lines; never lower the threshold. Commit when green (message: `move ragas
      scoring into src/utils/ragas_scoring.py`).

## 3. Unix-socket judge builder (parent task 1.4)

- [ ] 3.1 In `tests/unit/test_ragas_scoring.py`, add the two socket tests of design D6
      (key present: one `POST /v1/chat/completions` with `Authorization: Bearer k-test`;
      key unset or empty: `ValueError` that names the variable, and no request). Use a
      socket under `tempfile.mkdtemp(prefix="rj")`, a client `timeout` of 10 seconds,
      `max_retries=0`, and a `finally` that shuts the server down and joins its thread
      with `timeout=5`. Run the file and confirm the new tests FAIL. Then, in the same
      task, write `build_uds_judge_llm` in `src/utils/ragas_scoring.py` as design D6
      says. Re-run the file and confirm all tests pass. Tick task 1.4 in the parent
      `tasks.md`. Run `bash scripts/gate.sh` and commit when green (message: `add a
      unix-socket judge builder with an env key`).

## 4. Correct the parent change

- [ ] 4.1 In `openspec/changes/offload-ragas-judge-to-slurm/design.md`, append one short
      paragraph to D2. Say that PR 1 (#730) moved only the evaluate, judge and embedding
      wiring, that the pure helpers stay in `src/utils/benchmark_schema.py`, that the
      `Benchmarker` wrappers keep `_judge_usage`, `_ragas_scored` and
      `_ragas_judge_identity`, and that the builders take their constructors as
      arguments so the `service_benchmark` patch points still apply. Change nothing else
      in that file. Run `openspec validate offload-ragas-judge-to-slurm --strict` and
      `openspec validate fix-issue-730-ragas-scoring-extract --strict`, and confirm both
      pass. Run `bash scripts/gate.sh` and commit when green (message: `correct judge
      offload design d2 to the pr 1 scope`).

## 5. Publish

- [ ] 5.1 Run `bash scripts/gate.sh` once more on the branch tip and confirm it exits 0.
      Confirm each acceptance criterion of issue #730 against the tree. Push the branch:
      `git push -u origin fix/issue-730-ragas-scoring-extract`. Open the PR:
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-730-ragas-scoring-extract`
      with the title `refactor(benchmark): extract ragas scoring into
      src/utils/ragas_scoring.py (judge offload PR 1)` and `Closes #730` in the BODY
      (not the title). Pass the body with `--body` or a file under `/tmp`; do NOT commit
      a PR-body file to the repository. In the body: list the acceptance criteria with
      their evidence (test names, the gate result); give the SHA of the task 1.2 commit
      and say that the characterization test is unchanged since it; and say in one
      sentence that `openspec/changes/fix-issue-730-ragas-scoring-extract/` is a driver
      for the nightly executor and that `offload-ragas-judge-to-slurm` stays the design
      of record (issue #730 asked for no new change, and the branch name differs from
      the issue's `feat/ragas-scoring-extract` for the same reason). STOP at the open
      PR. Do not merge.
