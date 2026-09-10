# Tasks — a working judge client for the huggingface evaluator (issue #449)

Every checkbox below is one loop turn and ends **green and committed**. Write the failing test,
watch it fail for the stated reason, write the smallest fix, run `bash scripts/gate.sh`,
commit. Never end a task with the suite red, and never bypass the gate.

Standing notes for every task:

- **Scope.** The only files to edit are `src/bin/service_benchmark.py`,
  `tests/unit/test_ragas_evaluator_local_mode.py`, `docs/docs/benchmarking.md`, and this
  change's own directory. Do not edit `src/archi/providers/__init__.py` (design D2), do not
  edit `src/bin/benchmark_sut.py` (design D3), and do not touch any other `case` arm of
  `get_ragas_llm_evaluator`. Do not touch the control-plane or CI files the project rails
  protect.
- **Run `python -m pytest`, not bare `pytest`**, so an editable install cannot resolve `src`
  to a different checkout. The toolchain needs
  `PATH=/home/austin/miniforge3/envs/archi/bin:$PATH` in a bare shell.
- **Run `bash scripts/gate.sh` bare.** Do not pipe it and do not redirect it; the script
  refuses to run under a pipe. Read the persisted output instead.
- **Formatting.** Both Python files are black 24.10.0 and isort 6.0.1 clean today. Run `black`
  and `isort` on every file you edit **before** `git add`, so the pre-commit writer cannot
  leave content out of the commit, and check `git status` is empty after each commit.
- **Append, do not insert.** New tests go at the END of
  `tests/unit/test_ragas_evaluator_local_mode.py`, under a comment banner naming issue #449.
  The file's current last line, `    assert isinstance(llm, ChatOpenAI)` at line 64, must
  appear unchanged as trailing context in
  `git diff origin/dev -- tests/unit/test_ragas_evaluator_local_mode.py`. Nightly runs have
  inserted tests above a file's final line and swallowed the previous test's last assertion.
- **No duplicate test names.** The gate runs no linter, so a reused `def test_...` silently
  deletes the earlier test while staying green. The file collects 3 tests today; after each
  task it must collect 3 plus the number you have added, and you must read that number.
- **Reuse `_bench`; do not add a second helper.** It builds
  `{"mode_settings": {"ragas_settings": {}}, **benchmarking}`
  (`tests/unit/test_ragas_evaluator_local_mode.py:21-25`), so passing your own `mode_settings`
  **replaces** the empty default rather than merging into it. That is the intended way to set
  the `evaluator_*` keys through this helper:

      _bench({
          "model": "qwen-x",
          "mode_settings": {"ragas_settings": {
              "evaluator_provider": "huggingface",
              "evaluator_model": "judge-x",
              "evaluator_ollama_url": "http://judge-host:8001/v1",
          }},
      })

- **Assert the client, not the absence of a crash.** Read the base URL from
  `llm.openai_api_base`. A test that only asserts "no exception" passes for the wrong fix
  described in design D1.
- **Do not weaken the existing tests.** The three tests already in the file
  (`test_local_v1_judge_fallback_uses_openai_compatible_client`,
  `test_local_native_ollama_judge_fallback_uses_chatollama`,
  `test_local_explicit_provider_mode_forces_openai_compatible`) must pass unchanged at every
  commit.

## 1. The fix and its tests

- [x] 1.1 Make the `huggingface` arm return a client, in `src/bin/service_benchmark.py`.
      RED first: append to `tests/unit/test_ragas_evaluator_local_mode.py`, under a
      `# --- issue #449: the huggingface judge arm must build a client ---` banner, a test
      named `test_huggingface_judge_uses_the_configured_evaluator_url` that calls `_bench`
      with the `mode_settings` shape in the standing notes above
      (`evaluator_provider: "huggingface"`, `evaluator_model: "judge-x"`,
      `evaluator_ollama_url: "http://judge-host:8001/v1"`, and a top-level SUT
      `"model": "qwen-x"`), calls `bench.get_ragas_llm_evaluator()`, and asserts three things:
      the result `isinstance(llm, ChatOpenAI)`, `llm.openai_api_base ==
      "http://judge-host:8001/v1"`, and `llm.model_name == "judge-x"`. The test fails today
      with `TypeError: get_model() missing 1 required positional argument: 'provider_config'`;
      run it and watch that exact message before writing any source change.
      Then implement per design D1. Replace the arm's call body at
      `src/bin/service_benchmark.py:1417-1419` so the arm reads exactly:

          case "huggingface":
              base_url = ollama_url or "http://localhost:8000/v1"
              return get_model(
                  "local", model_name, {"base_url": base_url, "mode": "openai_compat"}
              )

      The dictionary key is `mode`, not `local_mode` — `get_model` translates `mode` into
      `extra_kwargs["local_mode"]` for LOCAL providers at
      `src/archi/providers/__init__.py:270-276`, and a `local_mode` key inside the dictionary
      passes no value through and silently yields a native Ollama client. Keep the `base_url`
      local variable; do not substitute `ollama_url`, which can be `None` here. Change nothing
      else in the file. The replacement line is 88 characters, which is black's limit, so the
      file does not reflow — confirm with `black --check src/bin/service_benchmark.py`.
      Re-run `python -m pytest tests/unit/test_ragas_evaluator_local_mode.py -q`, confirm
      4 tests pass, then run `bash scripts/gate.sh` and commit.

- [ ] 1.2 Pin the remaining three configuration routes, in
      `tests/unit/test_ragas_evaluator_local_mode.py` only. These tests pass once 1.1 has
      landed — they are over-reach guards, so do not contrive a failure for any of them, and
      say so in each docstring. Append after the 1.1 test:
      (a) `test_huggingface_judge_defaults_to_the_local_openai_compatible_port` — `_bench` with
      `evaluator_provider: "huggingface"`, `evaluator_model: "judge-x"`, no
      `evaluator_ollama_url`, and no top-level `ollama_url`. Assert `ChatOpenAI`,
      `openai_api_base == "http://localhost:8000/v1"`, and `model_name == "judge-x"`.
      (b) `test_huggingface_sut_provider_key_reaches_the_judge_arm` — `_bench` with a top-level
      `{"provider": "huggingface", "model": "qwen-x"}` and no `mode_settings` override at all,
      so the fallback chain at `src/bin/service_benchmark.py:1371-1377` supplies the provider.
      Assert `ChatOpenAI`, `openai_api_base == "http://localhost:8000/v1"`, and
      `model_name == "qwen-x"`.
      (c) `test_huggingface_judge_inherits_the_sut_url_when_no_judge_url_is_set` — `_bench`
      with `{"provider": "huggingface", "model": "qwen-x", "ollama_url":
      "http://sut-host:9000/v1"}`. Assert the base URL is `http://sut-host:9000/v1`, not the
      default.
      Then prove the whole set binds to this defect: revert the one source line to the broken
      keyword-argument form, run
      `python -m pytest tests/unit/test_ragas_evaluator_local_mode.py -q`, and confirm all four
      new tests fail with the `TypeError` while the three pre-existing tests still pass.
      Restore the fix and confirm 7 tests pass before you go on — **do not commit while the
      line is reverted.** Read the collected count and confirm it is 7. Gate green; commit.

## 2. Documentation

- [ ] 2.1 Document the provider, in `docs/docs/benchmarking.md`. Add one paragraph to the
      "Judge/SUT split" section (`docs/docs/benchmarking.md:435`), immediately after the
      paragraph that begins "The `huit_bedrock` provider is Harvard's Anthropic-compatible
      Bedrock proxy". State that `evaluator_provider: huggingface` names any
      OpenAI-compatible judge endpoint — the convention vLLM and TGI serve — that the endpoint
      comes from `evaluator_ollama_url`, falling back to the system-under-test `ollama_url`
      and then to `http://localhost:8000/v1`, and that the provider always builds an
      OpenAI-compatible client, so it must not be pointed at a native Ollama port. Do not add
      an example block, do not edit the `huit_bedrock` example, and change no other section.
      `AGENTS.md:53` is the rule this satisfies. Markdown is not linted by the gate, but run
      `bash scripts/gate.sh` anyway and commit.

## 3. Close out

- [ ] 3.1 Verify, push, and open the PR. Steps, in order:
      1. `bash scripts/gate.sh` on the finished branch exits 0. `git status` is empty.
      2. `git diff origin/dev --stat` lists only `src/bin/service_benchmark.py`,
         `tests/unit/test_ragas_evaluator_local_mode.py`, `docs/docs/benchmarking.md`, and
         this change's `openspec/changes/fix-issue-449-huggingface-evaluator-get-model/`
         files. Confirm `git diff origin/dev -- src/archi/ src/bin/benchmark_sut.py` prints
         nothing, and that the `src/bin/service_benchmark.py` diff is one changed line.
      3. `openspec validate fix-issue-449-huggingface-evaluator-get-model --strict` exits 0.
         If the `openspec` CLI is not installed in this environment, skip this step and say so
         in the PR body — do not install anything.
      4. Push: `git push -u origin fix/issue-449-huggingface-evaluator-get-model`. Confirm
         `git ls-remote origin fix/issue-449-huggingface-evaluator-get-model` reports the same
         SHA as local `HEAD`; a push that reports "up-to-date" without matching SHAs did not
         land.
      5. Open the PR with
         `gh pr create --repo fasrc/archi --base dev --title "fix(#449): build a real judge client for the huggingface RAGAS evaluator"`.
         The body MUST contain `Closes #449` on its own line — a closing keyword in the title
         does not link the issue — and MUST contain these sections:
         **What** (one line in one `case` arm: the configuration dictionary is passed
         positionally, with key `mode` not `local_mode`);
         **Why it never worked** (`get_model` requires `provider_config` positionally, so the
         arm raised `TypeError` before the function body ran, on all three configuration
         routes);
         **Measured** (the four-route table from `proposal.md`: `TypeError` before, and after
         the fix a `ChatOpenAI` at the configured URL, at the `http://localhost:8000/v1`
         default, through the SUT `provider` key, and inheriting the SUT `ollama_url`);
         **Blast radius** (latent — no shipped config selects `huggingface`; the docs and both
         example configs use `huit_bedrock`);
         **Scope against the open PRs** (no open PR touches any file this change edits;
         checked by file list against #453, #455, and #456);
         **Tests** (the four added tests, the revert-proof from task 1.2, and the collected
         count the gate reported).
         If `gh pr create` against `fasrc/archi` fails with a permissions error, leave the
         branch pushed, do **not** open a PR on any other repository, and stop.
      6. Record the PR URL as a line under this task, tick the task, and commit that edit with
         the gate. Do not merge.
